import socket
import threading
from collections.abc import Iterator

import pytest
import zmq

import common.bus as bus_mod
from common.bus import Publisher, Subscriber
from common.schema import BleHit, WifiHit

HIT = BleHit(dev="abc123", rssi_dbm=-70, rssi_smooth=-71, est_range_m=5)
WIFI = WifiHit(dev="abc123", rssi_dbm=-75, rssi_smooth=-74, est_range_m=7, channel=1)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A proxy on free ports, so tests never touch a running node."""
    ports = (_free_port(), _free_port())
    monkeypatch.setattr(bus_mod, "bus_ports", lambda bus: ports)
    ctx = zmq.Context()
    thread = threading.Thread(target=bus_mod.run_proxy, args=("node", ctx), daemon=True)
    thread.start()
    yield
    ctx.term()
    thread.join(timeout=2)


def _first(sub: Subscriber, pub: Publisher, sends: list[tuple[str, BleHit | WifiHit]]):
    """Publish until something arrives (the first sends can race the connection)."""
    for _ in range(30):
        for topic, msg in sends:
            pub.publish(topic, msg)
        got = sub.recv(timeout_s=0.1)
        if got is not None:
            return got
    return None


def test_message_crosses_the_bus(proxy: None) -> None:
    with Subscriber(["find.ble"]) as sub, Publisher("ble_scanner") as pub:
        got = _first(sub, pub, [("find.ble", HIT)])
    assert got is not None
    topic, msg = got
    assert topic == "find.ble"
    assert msg == HIT.model_copy(update={"src": "ble_scanner"})


def test_publisher_keeps_an_existing_src(proxy: None) -> None:
    with Subscriber([""]) as sub, Publisher("replay") as pub:
        got = _first(
            sub, pub, [("find.ble", HIT.model_copy(update={"src": "ble_scanner"}))]
        )
    assert got is not None
    assert got[1].src == "ble_scanner"


def test_subscriber_only_gets_its_topics(proxy: None) -> None:
    with Subscriber(["find.ble"]) as sub, Publisher("test") as pub:
        assert _first(sub, pub, [("find.wifi", WIFI), ("find.ble", HIT)]) is not None
        while (got := sub.recv(timeout_s=0.2)) is not None:
            assert got[0] == "find.ble"


def test_publish_rejects_the_wrong_model(proxy: None) -> None:
    with Publisher("test") as pub, pytest.raises(ValueError, match="doesn't belong"):
        pub.publish("find.wifi", HIT)


def test_subscriber_rejects_unknown_topics() -> None:
    with pytest.raises(ValueError, match="no topic matches"):
        Subscriber(["find.gps"])
    with pytest.raises(ValueError, match="at least one"):
        Subscriber([])
