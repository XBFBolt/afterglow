"""The thin end-to-end path, in one process: the fake scenario through
fusion's logic, lora_tx, the sim radio (real UDP on loopback), lora_rx and the
command post's board. Only the bus is faked."""

import random
import time

from common.config import Config
from common.schema import Message, Target
from fuse import fusion
from map.board import Board
from relay import lora_rx, lora_tx
from relay.radio import SimRadio, link_flag
from tests.fakes import FakePub, FakeSub
from tools import fake_detections as fake


def _fused_targets(cfg: Config) -> list[Target]:
    tracker = fusion.new_tracker(cfg)
    sent: list[Target] = []
    for _, msg in fake.script(random.Random(7)):
        sent += fusion.on_message(tracker, msg, msg.t, cfg)
        sent += fusion.due(tracker, msg.t, cfg)
    return sent


class Link:
    """Both ends of the relay, with the command post's board."""

    def __init__(self, cfg: Config, radios: tuple[SimRadio, SimRadio]) -> None:
        self.node, self.post = radios
        self.tx = lora_tx.new_state(cfg, sim=True)
        self.rx = lora_rx.RxState(cfg, sim=True)
        self.board = Board(cfg)
        self.arrived: list[Message] = []

    def run(self, msgs: list[tuple[str, Message]], seconds: float = 0.3) -> None:
        """Feed msgs to lora_tx, then let both ends work for a while."""
        sub, end = FakeSub(msgs), time.monotonic() + seconds
        while sub.msgs or time.monotonic() < end:
            lora_tx.step(sub, FakePub(), self.node, self.tx)
            pub = FakePub()
            lora_rx.step(self.post, pub, self.rx)
            for _, msg in pub.sent:
                self.arrived.append(msg)
                self.board.heard(msg, time.monotonic(), time.time())


def test_fake_scenario_ends_with_a_p1_at_the_command_post(
    cfg: Config, radios: tuple[SimRadio, SimRadio]
) -> None:
    targets = _fused_targets(cfg)
    link = Link(cfg, radios)
    link.run([("fuse.target", target) for target in targets])
    state = link.board.snapshot(time.monotonic())
    assert state["link"] == "up" and state["sim"] is True
    [victim] = state["victims"]
    assert victim["p"] == 1 and victim["alive"]
    assert victim["br"] is not None and 13 <= victim["br"] <= 15
    assert victim["rng"] is not None and victim["rng"] <= 3
    # Every alert fusion sent arrived, in order, and nothing was left queued.
    alerts = [msg.p for msg in link.arrived if msg.__class__.__name__ == "Alert"]
    assert alerts == [target.priority for target in targets]
    assert len(link.tx.queue) == 0


def test_after_a_link_drop_the_queued_p1_arrives_first(
    cfg: Config, radios: tuple[SimRadio, SimRadio]
) -> None:
    link = Link(cfg, radios)
    link.run([])  # link up, beacons flowing
    link_flag(cfg).touch()
    queued = [
        Target(t=1, id=1, confidence=0.6, priority=3, alive=True),
        Target(t=2, id=2, confidence=0.3, priority=4, alive=False),
        Target(t=3, id=3, confidence=0.9, priority=1, alive=True),
    ]
    link.run([("fuse.target", target) for target in queued])
    assert link.board.markers == {}
    link_flag(cfg).unlink()
    link.run([], seconds=1)
    ids = [msg.id for msg in link.arrived if hasattr(msg, "id")]
    assert ids == [3, 1, 2]
