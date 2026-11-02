"""What the command post knows, built from the post bus: the latest alert for
each track, the node's status from its beacons, and the post's own modules'
heartbeats. Pure logic, no I/O; the web map shows snapshot().

The command post must never show silence as good news. With no beacon for
relay.link_dead_s the link is down, the node's modules become unknown (not
"ok"), and every marker says how long ago it was heard.

Each marker keeps a short history, one step whenever its priority changed or
its breathing rate first arrived, so the operator (and the judges) can see a
faint phone climb to "alive, breathing 14/min".

Times are time.monotonic() for ages, so a clock set mid-search can't make old
news look fresh, plus time.time() of arrival for the operator to read.
"""

import math
import time
from dataclasses import dataclass, field
from typing import Any

from common.config import Config
from common.health import Watch, states_from_bits
from common.schema import Alert, Beacon, Heartbeat, Message

HAZARD_NAME = {0: "none", 1: "caution", 2: "danger"}
HISTORY_MAX = 10  # steps kept per marker; the first always stays


@dataclass
class Marker:
    alert: Alert
    heard_t: float  # monotonic
    heard_wall: float  # time.time()
    history: list[tuple[Alert, float]] = field(default_factory=list)  # (alert, wall)


class Board:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.markers: dict[int, Marker] = {}  # by track id
        self.beacon: Beacon | None = None
        self.beacon_t = -math.inf  # monotonic
        self.missed = 0  # beacons lost in the air, from gaps in seq
        self.post = Watch(cfg.health.post_modules, cfg.health.down_after_s)

    def heard(self, msg: Message, now: float, wall: float) -> None:
        match msg:
            case Alert():
                # The newest arrival wins, whatever its `t`: a node rebooted
                # without a clock must not have its alerts ignored.
                old = self.markers.get(msg.id)
                history = [] if old is None else old.history
                if old is None or _news(old.alert, msg):
                    history = [*history, (msg, wall)]
                    if len(history) > HISTORY_MAX:
                        history = history[:1] + history[1 - HISTORY_MAX :]
                self.markers[msg.id] = Marker(msg, now, wall, history)
                self._bound()
            case Beacon():
                if self.beacon is not None and msg.seq > self.beacon.seq:
                    self.missed += msg.seq - self.beacon.seq - 1
                # A lower seq is a restarted relay (or a wrap): nothing missed.
                self.beacon, self.beacon_t = msg, now
            case Heartbeat():
                self.post.heard(msg, now)

    def _bound(self) -> None:
        """Keep at most tracks.max_tracks markers: the least urgent, longest
        quiet go first."""
        while len(self.markers) > self.cfg.tracks.max_tracks:
            drop = max(self.markers.values(), key=lambda m: (m.alert.p, -m.heard_t))
            del self.markers[drop.alert.id]

    def link(self, now: float) -> str:
        """The link: up, down, or waiting (no beacon yet since this started)."""
        if self.beacon is None:
            return "waiting"
        return "up" if now - self.beacon_t <= self.cfg.relay.link_dead_s else "down"

    def snapshot(self, now: float) -> dict[str, Any]:
        """Everything the operator sees, as plain JSON-ready data."""
        link = self.link(now)
        names = self.cfg.health.node_modules
        if link == "up" and self.beacon is not None:
            node = states_from_bits(names, self.beacon.up, self.beacon.bad)
        else:
            node = dict.fromkeys(names, "unknown")
        markers = sorted(self.markers.values(), key=lambda m: (m.alert.p, -m.heard_t))
        return {
            "link": link,
            "beacon_age_s": None if self.beacon is None else now - self.beacon_t,
            "link_dead_s": self.cfg.relay.link_dead_s,
            "missed": self.missed,
            "sim": None if self.beacon is None else self.beacon.sim,
            "node": [{"name": k, "state": v} for k, v in node.items()],
            "post": [{"name": k, "state": v} for k, v in self.post.states(now).items()],
            "victims": [_victim(m, now) for m in markers],
        }


def _news(old: Alert, new: Alert) -> bool:
    """A step worth showing in the history."""
    return new.p != old.p or (new.br is not None and old.br is None)


def _clock(wall: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(wall))


def _victim(marker: Marker, now: float) -> dict[str, Any]:
    a = marker.alert
    return {
        "id": a.id,
        "p": a.p,
        "c": a.c,
        "alive": a.alive,
        "br": a.br,
        "hz": None if a.hz is None else HAZARD_NAME[a.hz],
        "rng": a.rng,
        "heard_s": now - marker.heard_t,
        "heard_at": _clock(marker.heard_wall),
        "history": [
            {"p": step.p, "c": step.c, "br": step.br, "at": _clock(wall)}
            for step, wall in marker.history
        ],
    }
