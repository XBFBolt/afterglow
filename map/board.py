"""What the command post knows, built from the post bus: the latest alert for
each track, the node's status from its beacons, and the post's own modules'
heartbeats. Pure logic, no I/O; the web map shows snapshot().

The command post must never show silence as good news. With no beacon for
relay.link_dead_s the link is down, the node's modules become unknown (not
"ok"), and every marker says how long ago it was heard.

Times are time.monotonic() for ages, so a clock set mid-search can't make old
news look fresh, plus time.time() of arrival for the operator to read.
"""

import math
import time
from dataclasses import dataclass
from typing import Any

from common.config import Config
from common.health import Watch, states_from_bits
from common.schema import Alert, Beacon, Heartbeat, Message

HAZARD_NAME = {0: "none", 1: "caution", 2: "danger"}


@dataclass
class Marker:
    alert: Alert
    heard_t: float  # monotonic
    heard_wall: float  # time.time()


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
                self.markers[msg.id] = Marker(msg, now, wall)
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
        "heard_at": time.strftime("%H:%M:%S", time.localtime(marker.heard_wall)),
    }
