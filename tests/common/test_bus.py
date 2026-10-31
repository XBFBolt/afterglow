import socket
import threading
from collections.abc import Iterator

import pytest
import zmq

import common.bus as bus_mod
from common.bus import Publisher, Subscriber
from common.schema import BleHit, Message, WifiHit

HIT = BleHit(dev="abc12345de", rssi_dbm=-70, rssi_smooth=-71, est_range_m=5)
WIFI = WifiHit(
    dev="abc12345de", rssi_dbm=-75, rssi_smooth=-74, est_range_m=7, channel=1
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_proxy() -> tuple[zmq.Context, threading.Thread]:
    ctx = zmq.Context()
    thread = threading.Thread(target=bus_mod.run_proxy, args=("node", ctx), daemon=True)
    thread.start()
    return ctx, thread


def _stop_proxy(ctx: zmq.Context, thread: threading.Thread) -> None:
    ctx.term()
    thread.join(timeout=2)


@pytest.fixture
def ports(monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    """Free ports, so tests never touch a running node."""
    ports = (_free_port(), _free_port())
    monkeypatch.setattr(bus_mod, "bus_ports", lambda bus: ports)
    return ports


@pytest.fixture
def proxy(ports: tuple[int, int]) -> Iterator[tuple[int, int]]:
    ctx, thread = _start_proxy()
    yield ports
    _stop_proxy(ctx, thread)


def _first(
    sub: Subscriber, pub: Publisher, sends: list[tuple[str, Message]]
) -> tuple[str, Message] | None:
    """Publish until something arrives (the first sends can race the connection)."""
    for _ in range(30):
        for topic, msg in sends:
            pub.publish(topic, msg)
        got = sub.recv(timeout_s=0.1)
        if got is not None:
            return got
    return None


def test_message_crosses_the_bus(proxy: tuple[int, int]) -> None:
    with Subscriber(["find.ble"]) as sub, Publisher("ble_scanner") as pub:
        got = _first(sub, pub, [("find.ble", HIT)])
    assert got is not None
    topic, msg = got
    assert topic == "find.ble"
    assert msg == HIT.model_copy(update={"src": "ble_scanner"})


def test_publisher_keeps_an_existing_src(proxy: tuple[int, int]) -> None:
    with Subscriber([""]) as sub, Publisher("replay") as pub:
        got = _first(
            sub, pub, [("find.ble", HIT.model_copy(update={"src": "ble_scanner"}))]
        )
    assert got is not None
    assert got[1].src == "ble_scanner"


def test_subscriber_only_gets_its_topics(proxy: tuple[int, int]) -> None:
    with Subscriber(["find.ble"]) as sub, Publisher("test") as pub:
        assert _first(sub, pub, [("find.wifi", WIFI), ("find.ble", HIT)]) is not None
        while (got := sub.recv(timeout_s=0.2)) is not None:
            assert got[0] == "find.ble"


def test_publish_rejects_the_wrong_model(proxy: tuple[int, int]) -> None:
    with Publisher("test") as pub, pytest.raises(ValueError, match="doesn't belong"):
        pub.publish("find.wifi", HIT)


def test_subscriber_rejects_unknown_topics() -> None:
    with pytest.raises(ValueError, match="no topic matches"):
        Subscriber(["find.gps"])
    with pytest.raises(ValueError, match="at least one"):
        Subscriber([])


def test_bad_messages_are_dropped_not_passed_on(proxy: tuple[int, int]) -> None:
    raw = zmq.Context.instance().socket(zmq.PUB)
    raw.connect(f"tcp://127.0.0.1:{proxy[0]}")
    garbage = [
        [b"find.ble", b"not json"],
        [b"find.ble", b'{"dev": "aa:bb:cc:dd:ee:ff", "rssi_dbm": -70}'],  # raw MAC
        [b"find.ble", HIT.model_dump_json().replace("-70.0", "NaN").encode()],
        [b"find.bless", HIT.model_dump_json().encode()],  # unknown topic
        [b"find.ble"],  # missing body
        [b"find.ble", b"{}", b"extra frame"],
    ]
    good = [b"find.ble", HIT.model_dump_json().encode()]
    try:
        with Subscriber(["find."]) as sub:
            got = None
            for _ in range(30):
                for frames in garbage:
                    raw.send_multipart(frames)
                raw.send_multipart(good)
                got = sub.recv(timeout_s=0.1)
                if got is not None:
                    break
            assert got == ("find.ble", HIT)
            while (more := sub.recv(timeout_s=0.2)) is not None:
                assert more == ("find.ble", HIT)
    finally:
        raw.close(linger=0)


def test_bus_recovers_when_the_proxy_restarts(ports: tuple[int, int]) -> None:
    ctx, thread = _start_proxy()
    with Subscriber(["find.ble"]) as sub, Publisher("ble_scanner") as pub:
        assert _first(sub, pub, [("find.ble", HIT)]) is not None
        _stop_proxy(ctx, thread)
        ctx, thread = _start_proxy()
        try:
            assert _first(sub, pub, [("find.ble", HIT)]) is not None
        finally:
            _stop_proxy(ctx, thread)
