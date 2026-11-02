import logging
import time

import pytest

from common.config import Config
from common.schema import Alert, Beacon, Heartbeat, Target
from relay import lora_tx
from relay.codec import decode
from relay.radio import Packet, SimRadio, link_flag
from tests.fakes import FakePub, FakeSub


def _target(track: int = 7, priority: int = 1) -> Target:
    return Target(t=100.0, id=track, confidence=0.9, priority=priority, alive=True)


def _heard(post: SimRadio) -> list[Alert | Beacon]:
    out = []
    while isinstance(event := post.recv(0.2), Packet):
        out.append(decode(event.data))
    return out


def test_a_target_goes_out_as_an_alert_before_the_beacon(
    cfg: Config, radios: tuple[SimRadio, SimRadio]
) -> None:
    node, post = radios
    state = lora_tx.new_state(cfg, sim=True)
    pub = FakePub()
    lora_tx.step(FakeSub([("fuse.target", _target())]), pub, node, state)
    alert, beacon = _heard(post)
    assert isinstance(alert, Alert) and (alert.id, alert.p, alert.c) == (7, 1, 90)
    assert isinstance(beacon, Beacon) and beacon.seq == 0
    assert pub.on("relay.out") == [alert.model_copy(update={"t": 100.0})]
    lora_tx.step(FakeSub(), pub, node, state)  # collects the ack
    assert len(state.queue) == 0


def test_no_ack_means_sent_again_until_one_comes(
    cfg: Config, radios: tuple[SimRadio, SimRadio]
) -> None:
    node, post = radios
    state = lora_tx.new_state(cfg, sim=True)
    pub = FakePub()
    link_flag(cfg).touch()
    lora_tx.step(FakeSub([("fuse.target", _target())]), pub, node, state)
    deadline = time.monotonic() + 2
    while len(pub.on("relay.out")) < 3 and time.monotonic() < deadline:
        lora_tx.step(FakeSub(), pub, node, state)
    assert len(pub.on("relay.out")) == 3 and len(state.queue) == 1
    link_flag(cfg).unlink()
    while len(state.queue) and time.monotonic() < deadline + 2:
        lora_tx.step(FakeSub(), pub, node, state)
        post.recv(0)  # the post end acks what it hears
    assert len(state.queue) == 0  # delivered once the link came back


def test_the_beacon_reports_each_module(
    cfg: Config, radios: tuple[SimRadio, SimRadio]
) -> None:
    node, post = radios
    state = lora_tx.new_state(cfg, sim=False)
    beat = Heartbeat(src="fusion", status="degraded", sim=True, note="no frames")
    lora_tx.step(FakeSub([("health.beat", beat)]), FakePub(), node, state)
    [beacon] = _heard(post)
    assert isinstance(beacon, Beacon)
    names = cfg.health.node_modules
    fusion = 1 << names.index("fusion")
    assert beacon.up == fusion and beacon.bad == fusion  # recorder never beat: down
    assert beacon.sim  # because fusion runs on fake data


def test_beacons_keep_their_period(
    cfg: Config, radios: tuple[SimRadio, SimRadio], monkeypatch: pytest.MonkeyPatch
) -> None:
    node, post = radios
    clock = [0.0]
    monkeypatch.setattr(lora_tx.time, "monotonic", lambda: clock[0])
    state = lora_tx.new_state(cfg, sim=True)
    for _ in range(25):  # 25 s
        lora_tx.step(FakeSub(), FakePub(), node, state)
        clock[0] += 1
    beacons = _heard(post)
    assert [b.seq for b in beacons] == [0, 1, 2]  # at 0, 10 and 20 s


def test_an_alert_too_big_to_send_is_dropped_not_retried_forever(
    cfg: Config, radios: tuple[SimRadio, SimRadio], caplog: pytest.LogCaptureFixture
) -> None:
    node, post = radios
    state = lora_tx.new_state(cfg, sim=True)
    huge = _target(track=10**95)
    with caplog.at_level(logging.ERROR):
        lora_tx.step(FakeSub([("fuse.target", huge)]), FakePub(), node, state)
    assert len(state.queue) == 0 and state.unsendable == 1
    assert "over relay.max_alert_bytes" in caplog.text
    assert all(isinstance(msg, Beacon) for msg in _heard(post))
