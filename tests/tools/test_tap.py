from common.schema import BleHit, Target
from tools.tap import format_message


def test_line_shows_topic_source_and_fields() -> None:
    msg = BleHit(
        dev="2efae4bfcede1097",
        rssi_dbm=-64.0,
        rssi_smooth=-65.1234,
        est_range_m=4.0,
        src="ble",
    )
    line = format_message("find.ble", msg)
    assert "find.ble" in line
    assert " ble " in line
    assert "rssi_smooth=-65.12" in line
    assert "bearing_deg" not in line  # None fields are left out


def test_nested_floats_are_rounded() -> None:
    msg = Target(
        id=1, confidence=0.5, priority=3, alive=False, evidence={"rf": 0.123456}
    )
    assert "'rf': 0.12" in format_message("fuse.target", msg)
