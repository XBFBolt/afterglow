import socket
import xml.etree.ElementTree as ET
from datetime import datetime

from common.config import get_config
from common.schema import Alert, Beacon, RadarHit
from map import cot_sender
from map.cot_sender import open_socket, remarks, searcher_event, to_event, victim_event

CFG = get_config()
NOW = 1_793_594_786.0


def _parse(event: bytes) -> ET.Element:
    return ET.fromstring(event)


def _time(text: str | None) -> float:
    assert text is not None and text.endswith("Z")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def test_a_victim_marker() -> None:
    alert = Alert(t=NOW, id=701, p=1, c=89, rng=2, alive=True, br=14, hz=1)
    root = _parse(victim_event(alert, NOW, CFG))
    assert root.get("uid") == "AG-701" and root.get("type") == "a-n-G"
    assert _time(root.get("stale")) - _time(root.get("time")) == CFG.tak.stale_s
    point = root.find("point")
    assert point is not None and point.get("ce") == "2.0"
    assert float(point.get("lat") or 0) == CFG.post.searcher_lat
    contact = root.find("detail/contact")
    assert contact is not None and contact.get("callsign") == "P1 track 701"
    assert root.findtext("detail/remarks") == (
        "P1 URGENT: alive, breathing 14/min, confidence 89%, gas caution, "
        "within 2 m of searcher"
    )


def test_unknowns_say_unknown() -> None:
    alert = Alert(t=NOW, id=5, p=4, c=40, alive=False)
    root = _parse(victim_event(alert, NOW, CFG))
    point = root.find("point")
    assert point is not None and point.get("ce") == cot_sender.UNKNOWN
    assert remarks(alert) == (
        "P4 DEVICE ONLY: life not confirmed, breathing unknown, confidence 40%, "
        "gas: no reading, near searcher, range unknown"
    )


def test_the_searcher_is_a_friendly_marker() -> None:
    root = _parse(searcher_event(NOW, CFG))
    assert root.get("uid") == "AG-searcher" and root.get("type", "").startswith("a-f")


def test_which_messages_make_markers() -> None:
    assert to_event(Alert(id=1, p=2, c=90, alive=True), NOW, CFG) is not None
    assert to_event(Beacon(seq=1, up=1, bad=0, sim=True), NOW, CFG) is not None
    assert to_event(RadarHit(present=False, conf=0), NOW, CFG) is None


def test_shipped_config_keeps_cot_on_this_laptop() -> None:
    sock, dest = open_socket(CFG)
    try:
        assert dest == ("239.2.3.1", 6969)
        iface = sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, 4)
        assert socket.inet_ntoa(iface) == "127.0.0.1"
    finally:
        sock.close()
