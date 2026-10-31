import json
import logging
from datetime import datetime
from pathlib import Path

import pytest

from common import recorder
from common.config import get_config
from common.recorder import format_line, log_path, parse_line, record
from common.schema import Message, RadarHit, Target


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


@pytest.mark.parametrize(
    "line",
    [
        '{"topic": "find.ble", "msg": {"de',  # cut off by a power loss
        '{"msg": {}}',  # no topic
        "[1, 2]",  # JSON, wrong shape
        '{"topic": "find.gps", "msg": {}}',
        '{"topic": "confirm.radar", "msg": {"present": false}}',
    ],
)
def test_bad_lines_raise_value_error(line: str) -> None:
    # A replay skips a ValueError and carries on; anything else would stop it.
    with pytest.raises(ValueError):
        parse_line(line)


def test_log_path_names_run_by_start_time_and_bus() -> None:
    path = log_path(Path("logs"), "post", datetime(2026, 11, 7, 14, 3, 22))
    assert path == Path("logs/20261107-140322-post.jsonl")


class _Done(Exception):
    pass


def _fake_bus(
    monkeypatch: pytest.MonkeyPatch, msgs: list[Message], disk: list[bool]
) -> None:
    """Replace the bus with msgs, one per fsync interval, and the free-space
    check with disk. Stops record() when msgs run out."""
    clock = [0.0]
    pending = iter(msgs)
    free = iter(disk)

    class FakeSubscriber:
        def __init__(self, topics: list[str], bus: str) -> None:
            pass

        def __enter__(self) -> "FakeSubscriber":
            return self

        def __exit__(self, *exc: object) -> None:
            pass

        def recv(self, timeout_s: float) -> tuple[str, Message]:
            clock[0] += get_config().log.fsync_s
            msg = next(pending, None)
            if msg is None:
                raise _Done
            return "confirm.radar", msg

    monkeypatch.setattr(recorder, "Subscriber", FakeSubscriber)
    monkeypatch.setattr(recorder.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(recorder, "disk_ok", lambda path, mb: next(free))


def _radar(n: int) -> RadarHit:
    return RadarHit(t=n, present=False, conf=0.0)


def test_recording_pauses_on_low_disk_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    msgs = [_radar(n) for n in range(4)]
    _fake_bus(monkeypatch, msgs, disk=[True, False, True, True])
    path = tmp_path / "run.jsonl"
    with caplog.at_level(logging.INFO), pytest.raises(_Done):
        record("node", path)
    # Message 2 arrived while paused; the rest were recorded.
    recorded = [parse_line(line)[1] for line in path.read_text().splitlines()]
    assert recorded == [msgs[0], msgs[1], msgs[3]]
    assert "recording paused" in caplog.text
    assert "recording resumed" in caplog.text


def test_recorder_recreates_a_deleted_log_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_bus(monkeypatch, [_radar(0)], disk=[True])
    path = tmp_path / "deleted" / "run.jsonl"
    with pytest.raises(_Done):
        record("node", path)
    assert len(path.read_text().splitlines()) == 1
