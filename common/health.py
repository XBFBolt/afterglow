"""Heartbeats: every long-running module says it is still running, on topic
health.beat. A module that goes quiet or reports itself degraded must be
flagged to the operator, so a dead sensor never looks like "no victim here".
Heartbeats stay on their own bus; the node's reach the command post folded
into the status beacon (Watch.bits, sent by relay/lora_tx.py). Beats can
arrive up to about 2 x beat_s apart, so a module counts as down only after
health.down_after_s of silence.

    heart = Heart(pub, sim=args.sim)
    while True:
        ...one pass of the module's loop...
        heart.beat()                                       # all well
        heart.beat("degraded", "radar serial port missing")  # running, but blind

Call beat() on every pass of the main loop, and never block longer than
health.beat_s between calls (retry back-off included: sleep in short steps).
It runs in the loop, not in a thread, on purpose: a loop that hangs stops
beating, which is exactly what the command post needs to see.
"""

import logging
import math
import time
from typing import Literal

from pydantic import ValidationError

from common.bus import Publisher
from common.config import get_config
from common.schema import NOTE_MAX, Heartbeat, Status

log = logging.getLogger(__name__)

_RANK: dict[Status, int] = {"ok": 0, "degraded": 1}
State = Literal["ok", "degraded", "down"]


class Heart:
    """Publishes one module's Heartbeat at most once per health.beat_s."""

    def __init__(self, pub: Publisher, sim: bool) -> None:
        self._pub = pub
        self._sim = sim
        self._period = get_config().health.beat_s
        self._due = -math.inf  # beat on the first call
        self._status: Status = "ok"
        self._note: str | None = None

    def beat(self, status: Status = "ok", note: str | None = None) -> None:
        """Report the module's state. Publishes when a beat is due, with the
        worst status seen since the last beat, so a fault that clears between
        beats is still reported. A note too long is cut short; a note that looks
        like it holds a device address is left off, never published."""
        if _RANK[status] >= _RANK[self._status]:
            self._status, self._note = status, note
        now = time.monotonic()
        if now < self._due:
            return
        self._due = now + self._period
        note = None if self._note is None else self._note[:NOTE_MAX]
        try:
            msg = Heartbeat(status=self._status, sim=self._sim, note=note)
        except ValidationError:
            log.warning("heartbeat note looked like a device address; sent without it")
            msg = Heartbeat(status=self._status, sim=self._sim)
        self._status, self._note = "ok", None
        self._pub.publish("health.beat", msg)


class Watch:
    """Who is up, from the heartbeats of the modules named (others are
    ignored). Times are time.monotonic(): when each beat was heard."""

    def __init__(self, names: list[str], down_after_s: float) -> None:
        self.names = names
        self._down_after_s = down_after_s
        self._last: dict[str, tuple[float, Heartbeat]] = {}

    def heard(self, beat: Heartbeat, now: float) -> None:
        if beat.src in self.names:
            self._last[beat.src] = (now, beat)

    def states(self, now: float) -> dict[str, State]:
        """Each module's state; down if never heard or quiet for down_after_s."""
        out: dict[str, State] = {}
        for name in self.names:
            heard_t, beat = self._last.get(name, (-math.inf, None))
            if beat is None or now - heard_t > self._down_after_s:
                out[name] = "down"
            else:
                out[name] = beat.status
        return out

    def sim(self, now: float) -> bool:
        """Some module that is up runs on fake hardware or fake data."""
        states = self.states(now)
        return any(
            beat.sim for name, (_, beat) in self._last.items() if states[name] != "down"
        )

    def bits(self, now: float) -> tuple[int, int]:
        """(up, bad) for the status beacon: bit i is the i-th name."""
        up = bad = 0
        for i, state in enumerate(self.states(now).values()):
            if state != "down":
                up |= 1 << i
            if state == "degraded":
                bad |= 1 << i
        return up, bad


def states_from_bits(names: list[str], up: int, bad: int) -> dict[str, State]:
    """Inverse of Watch.bits, at the command post."""
    out: dict[str, State] = {}
    for i, name in enumerate(names):
        if not up >> i & 1:
            out[name] = "down"
        else:
            out[name] = "degraded" if bad >> i & 1 else "ok"
    return out
