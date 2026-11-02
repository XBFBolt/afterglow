import xml.etree.ElementTree as ET

import pytest

from common.config import get_config
from tools.cot_test import delete_event, marker_config

CFG = get_config()


def test_the_test_marker_goes_where_asked() -> None:
    cfg = marker_config(CFG, 40.7, -74.0, "192.168.1.42:4242", "192.168.1.10")
    assert (cfg.post.searcher_lat, cfg.post.searcher_lon) == (40.7, -74.0)
    assert cfg.tak.url == "udp://192.168.1.42:4242"
    assert cfg.tak.send_from == "192.168.1.10"


@pytest.mark.parametrize(
    "lat, lon, to, frm",
    [
        (91, 0, "239.2.3.1:6969", "192.168.1.10"),  # no such latitude
        (0, 0, "phone:4242", "192.168.1.10"),  # not an address
        (0, 0, "239.2.3.1:6969", "wifi"),
    ],
)
def test_bad_settings_are_refused(lat: float, lon: float, to: str, frm: str) -> None:
    with pytest.raises(ValueError):
        marker_config(CFG, lat, lon, to, frm)


def test_the_test_marker_fades_fast_and_can_be_deleted() -> None:
    cfg = marker_config(CFG, 40.7, -74.0, "239.2.3.1:6969", "192.168.1.10")
    assert cfg.tak.stale_s <= 10  # a real victim's is 2 minutes
    root = ET.fromstring(delete_event("AG-test", 1_793_594_786.0))
    assert root.get("type") == "t-x-d-d"
    link = root.find("detail/link")
    assert link is not None and link.get("uid") == "AG-test"
    assert root.find("detail/__forcedelete") is not None
