from pathlib import Path

from common.recorder import format_line
from common.schema import BleHit, HazardReading, Heartbeat, Target
from tools.replay import SENSOR_TOPICS, load

DEV = "0123456789abcdef"


def test_replays_sensor_topics_in_order_from_zero(tmp_path: Path) -> None:
    hit = BleHit(t=1000.5, dev=DEV, rssi_dbm=-60, rssi_smooth=-61, est_range_m=3)
    gas = HazardReading(t=1000.0, hazard="none", gas_raw=180)
    target = Target(t=1001.0, id=1, confidence=0.5, priority=4, alive=False)
    beat = Heartbeat(t=1000.2, status="ok", sim=True)
    path = tmp_path / "run.jsonl"
    path.write_text(
        format_line("find.ble", hit)
        + "not a recorded line\n"
        + format_line("fuse.target", target)  # fusion redoes this
        + format_line("health.beat", beat)
        + format_line("hazard.gas", gas)
        + '{"topic": "find.ble", "msg": {"dev": "aa:bb:cc:dd:ee:ff"}}\n'
    )
    events, bad = load(path, SENSOR_TOPICS)
    assert bad == 2
    assert [(topic, msg.t) for topic, msg in events] == [
        ("hazard.gas", 0.0),
        ("find.ble", 0.5),
    ]


def test_an_empty_recording_replays_nothing(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    assert load(path, SENSOR_TOPICS) == ([], 0)
