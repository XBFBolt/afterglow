import logging
import socket

import pytest

from common.config import Config
from common.schema import Alert, Beacon
from relay import lora_rx
from relay.codec import encode
from relay.radio import SimRadio
from tests.fakes import FakePub


def test_alerts_and_beacons_go_on_their_topics(
    cfg: Config, radios: tuple[SimRadio, SimRadio]
) -> None:
    node, post = radios
    alert = Alert(t=100, id=7, p=1, c=90, alive=True, br=14)
    beacon = Beacon(seq=3, up=1, bad=0, sim=True)
    node.send(encode(alert, 100), want_ack=True)
    node.send(encode(beacon, 100), want_ack=False)
    pub, state = FakePub(), lora_rx.RxState(cfg, sim=True)
    for _ in range(3):
        lora_rx.step(post, pub, state)
    assert pub.on("relay.in") == [alert]
    [got] = pub.on("relay.beacon")
    assert isinstance(got, Beacon) and got.seq == 3


def test_packets_that_arent_ours_are_dropped_and_counted(
    cfg: Config, radios: tuple[SimRadio, SimRadio], caplog: pytest.LogCaptureFixture
) -> None:
    node, post = radios
    for junk in (b"hello from another meshtastic user", b'A{"v":9}'):
        node.send(junk, want_ack=True)
    pub, state = FakePub(), lora_rx.RxState(cfg, sim=True)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            lora_rx.step(post, pub, state)
    assert pub.sent == []
    # Reported at once, then summarized: the second waits for the next report.
    assert caplog.text.count("dropped 1 packet(s)") == 1 and state.bad == 1


def test_nothing_heard_is_not_an_error(
    cfg: Config, radios: tuple[SimRadio, SimRadio]
) -> None:
    _, post = radios
    pub = FakePub()
    lora_rx.step(post, pub, lora_rx.RxState(cfg, sim=True))
    assert pub.sent == []


def test_a_raw_mac_in_a_packet_never_reaches_the_log(
    cfg: Config, radios: tuple[SimRadio, SimRadio], caplog: pytest.LogCaptureFixture
) -> None:
    _, post = radios
    mac = "aa:bb:cc:dd:ee:ff"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(
            b"D\x00\x00\x00\x00\x01" + f'A{{"id":"{mac}"}}'.encode(), post._post_addr
        )
    with caplog.at_level(logging.DEBUG):
        lora_rx.step(post, FakePub(), lora_rx.RxState(cfg, sim=True))
    assert mac not in caplog.text
