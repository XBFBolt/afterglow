"""Heartbeats: every long-running module says it is still running, on topic
health.beat. The command post flags a module that goes quiet or reports itself
degraded, so a dead sensor never looks like "no victim here".

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

from pydantic import ValidationError

from common.bus import Publisher
from common.config import get_config
from common.schema import NOTE_MAX, Heartbeat, Status

log = logging.getLogger(__name__)

_RANK: dict[Status, int] = {"ok": 0, "degraded": 1}


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
