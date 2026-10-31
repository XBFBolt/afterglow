import json
from datetime import datetime
from pathlib import Path

from common.recorder import format_line, log_path, parse_line
from common.schema import RadarHit, Target


def test_line_round_trip() -> None:
    msg = Target(id=3, confidence=0.82, priority=2, alive=True, evidence={"rf": 1.0})
    line = format_line("fuse.target", msg)
    assert line.endswith("\n") and line.count("\n") == 1
    assert parse_line(line) == ("fuse.target", msg)


def test_line_is_plain_json() -> None:
    msg = RadarHit(present=True, moving=False, range_m=2, conf=0.7, src="radar")
    rec = json.loads(format_line("confirm.radar", msg))
    assert rec["topic"] == "confirm.radar"
    assert rec["msg"]["src"] == "radar"


def test_log_path_names_run_by_start_time_and_bus() -> None:
    path = log_path(Path("logs"), "post", datetime(2026, 11, 7, 14, 3, 22))
    assert path == Path("logs/20261107-140322-post.jsonl")
