import pytest

from common.config import get_config
from tools.cot_test import marker_config

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
