import random
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import fuse.fusion as fusion
from common.config import Config, TriageConfig, Weights, get_config
from common.ids import hash_device
from common.schema import (
    BleHit,
    Evidence,
    Hazard,
    HazardReading,
    Heartbeat,
    Message,
    RadarHit,
    Target,
    ThermalHit,
)
from common.service import run_forever
from fuse.fusion import (
    confidence,
    due,
    load_next_id,
    new_state,
    on_message,
    radar_evidence,
    save_next_id,
    step,
    to_target,
    triage,
)
from fuse.tracks import Tracker
from tools import fake_detections as fake

W = Weights(rf=0.5, thermal=0.7, radar=0.7, audio=0.4)
TRIAGE = TriageConfig(confirmed=0.8, possible=0.5)
CFG = get_config()
PHONE = "1" * 16


def _tracker() -> Tracker:
    return Tracker(CFG.tracks, known=[])


def ble(t: float, rssi: float = -55, rng: float = 3.0, dev: str = PHONE) -> BleHit:
    return BleHit(t=t, dev=dev, rssi_dbm=rssi, rssi_smooth=rssi, est_range_m=rng)


def thermal(t: float, conf: float = 0.9, person: bool = True) -> ThermalHit:
    return ThermalHit(t=t, person=person, conf=conf, bearing_deg=40, max_temp_c=34)


def radar(
    t: float, moving: bool = False, breaths: float | None = None, conf: float = 0.8
) -> RadarHit:
    return RadarHit(
        t=t,
        present=True,
        moving=moving,
        range_m=2.0,
        breaths_per_min=breaths,
        conf=conf,
    )


def _feed(tracker: Tracker, msgs: list[Message]) -> list[Target]:
    sent: list[Target] = []
    for msg in msgs:
        sent += on_message(tracker, msg, msg.t, CFG)
    return sent


# Score and triage


def test_worked_example_from_the_blueprint() -> None:
    assert confidence(Evidence(rf=1.0), W) == pytest.approx(0.5)
    assert confidence(Evidence(rf=1.0, thermal=0.9), W) == pytest.approx(0.815)
    assert confidence(Evidence(rf=1.0, thermal=0.9, radar=0.8), W) == pytest.approx(
        0.9186
    )


def test_sensors_with_no_evidence_are_left_out() -> None:
    assert confidence(Evidence(), W) == 0
    assert confidence(Evidence(rf=0.0), W) == 0
    assert confidence(Evidence(audio=1.0), W) == pytest.approx(0.4)


@pytest.mark.parametrize(
    ("c", "moving", "hazard", "priority"),
    [
        (0.85, False, None, 1),
        (0.8, False, None, 1),  # boundary
        (0.85, None, None, 2),  # unknown movement is not "still"
        (0.85, True, None, 2),
        (0.85, True, "danger", 1),
        (0.85, None, "caution", 2),
        (0.79, False, "danger", 3),  # danger alone doesn't confirm a person
        (0.5, None, None, 3),
        (0.49, None, None, 4),
        (0.0, None, None, 4),
    ],
)
def test_triage(
    c: float, moving: bool | None, hazard: Hazard | None, priority: int
) -> None:
    assert triage(c, moving, hazard, TRIAGE) == priority


@pytest.mark.parametrize(
    ("breaths", "e"), [(14, 1.0), (6, 1.0), (30, 1.0), (40, 0.55), (None, 0.55)]
)
def test_radar_evidence(breaths: float | None, e: float) -> None:
    hit = radar(0, breaths=breaths, conf=0.55)
    assert radar_evidence(hit, CFG.fusion) == e


# The test plan's false positives, with the shipped weights


def test_hot_object_alone_stays_below_confirmed() -> None:
    sent = _feed(_tracker(), [thermal(0, conf=1.0)])
    assert sent[-1].confidence < CFG.triage.confirmed
    assert sent[-1].priority >= 3


def test_phone_on_a_table_with_no_person_stays_p3_or_p4() -> None:
    sent = _feed(_tracker(), [ble(t, rssi=-40, rng=0.5) for t in range(100)])
    assert {t.priority for t in sent} <= {3, 4}
    assert not any(t.alive for t in sent)


def test_thermal_and_radar_misses_are_not_evidence_of_absence() -> None:
    tracker = _tracker()
    empty_room = RadarHit(t=1, present=False, conf=0.05)
    sent = _feed(tracker, [thermal(0, person=False), empty_room])
    assert sent == [] and tracker.tracks == {}


# Send rules


def test_send_on_new_track_and_on_priority_change_only() -> None:
    tracker = _tracker()
    sent = _feed(tracker, [ble(0), ble(1), ble(2)])  # P3, no change
    assert [t.priority for t in sent] == [3]
    sent = _feed(tracker, [thermal(3)])  # rf + thermal: confirmed, movement unknown
    assert [t.priority for t in sent] == [2]
    sent = _feed(tracker, [radar(4, moving=False)])
    assert [t.priority for t in sent] == [1]
    sent = _feed(tracker, [radar(5, moving=True)])  # stirred: no longer "still"
    assert [t.priority for t in sent] == [2]


def test_danger_makes_a_moving_confirmed_person_p1() -> None:
    tracker = _tracker()
    _feed(tracker, [ble(0), thermal(1), radar(2, moving=True)])
    hazard = HazardReading(t=3, hazard="danger", gas_raw=900)
    [target] = on_message(tracker, hazard, 3, CFG)
    assert target.priority == 1 and target.hazard == "danger"


def test_p1_and_p2_resend_every_resend_s_and_p3_p4_never() -> None:
    tracker = _tracker()
    _feed(tracker, [ble(0), thermal(1), radar(2)])  # P1
    _feed(tracker, [ble(3, dev="2" * 16, rssi=-85, rng=20)])  # P4
    resend = CFG.tracks.resend_s
    assert due(tracker, 2 + resend - 0.1, CFG) == []
    [target] = due(tracker, 2 + resend, CFG)
    assert target.priority == 1
    assert due(tracker, 2 + resend + 1, CFG) == []  # just sent
    assert len(due(tracker, 2 + 2 * resend, CFG)) == 1


def test_stale_geometry_becomes_unknown_but_the_person_stays_confirmed() -> None:
    tracker = _tracker()
    _feed(tracker, [ble(0), thermal(1), radar(2, breaths=14)])
    [track] = tracker.tracks.values()
    fresh = to_target(track, 3, CFG)
    assert fresh.bearing_deg == 40 and fresh.range_m == 2.0
    later = to_target(track, 600, CFG)  # searcher left ten minutes ago
    assert later.bearing_deg is None and later.range_m is None
    assert later.priority == 1 and later.alive and later.breaths_per_min == 14
    assert later.confidence == fresh.confidence


def test_a_clock_set_backwards_neither_freshens_old_readings_nor_stalls_resends() -> (
    None
):
    tracker = _tracker()
    _feed(tracker, [ble(5000), thermal(5001), radar(5002)])  # P1
    [track] = tracker.tracks.values()
    back = 5002 - 3600  # operator sets the clock back an hour
    target = to_target(track, back, CFG)
    assert target.bearing_deg is None and target.range_m is None
    [resent] = due(tracker, back, CFG)
    assert resent.priority == 1
    # A new heat source at the old bearing is not merged on a stale bearing.
    assert tracker.add_thermal(thermal(back), 0.9) is not track


def test_range_falls_back_to_the_rough_rf_estimate() -> None:
    tracker = _tracker()
    _feed(tracker, [ble(0, rng=4.2)])
    [track] = tracker.tracks.values()
    assert to_target(track, 1, CFG).range_m == 4.2
    assert to_target(track, 1, CFG).breaths_per_min is None  # not 0


# The fake scenario, end to end through fusion's logic


def test_fake_scenario_climbs_to_p1() -> None:
    tracker = Tracker(CFG.tracks, CFG.targets.devices)
    sent: list[Target] = []
    for _, msg in fake.script(random.Random(7)):
        sent += on_message(tracker, msg, msg.t, CFG)
        sent += due(tracker, msg.t, CFG)
        tracker.prune(msg.t)
    victim_dev = hash_device(fake.VICTIM_MAC)
    [victim] = [tr for tr in tracker.tracks.values() if victim_dev in tr.rf]
    story = [t for t in sent if t.id == victim.id]
    priorities = [t.priority for t in story]
    # Starts as a faint device, only ever gets more urgent, ends urgent.
    assert priorities[0] == 4 and priorities[-1] == 1
    assert priorities == sorted(priorities, reverse=True)
    assert 3 in priorities
    # Confirmed only once heat appears; P1 only once radar says still.
    assert all(t.t >= fake.THERMAL_FROM_S for t in story if t.priority <= 2)
    assert all(t.t >= fake.RADAR_FROM_S for t in story if t.priority == 1)
    last = story[-1]
    assert last.alive and last.hazard == "caution"
    assert last.breaths_per_min is not None and 13 <= last.breaths_per_min <= 15
    assert last.bearing_deg is not None
    assert abs(last.bearing_deg - fake.VICTIM_BEARING_DEG) <= 5
    # Every piece of evidence landed on the victim or the bystander.
    assert len(tracker.tracks) == 2
    others = [t for t in sent if t.id != victim.id]
    assert {t.priority for t in others} == {4}


# The loop: timestamps, track ids, crashes


class FakeSub:
    """Hands out scripted messages; an exception in the script is raised."""

    def __init__(self, script: Iterator[object]) -> None:
        self._script = script

    def recv(self, timeout_s: float | None = None) -> tuple[str, Message] | None:
        item = next(self._script, KeyboardInterrupt())
        if isinstance(item, BaseException):
            raise item
        return item  # type: ignore[return-value]

    def __enter__(self) -> "FakeSub":
        return self

    def __exit__(self, *exc: object) -> None:
        pass


class FakePub:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Message]] = []

    def publish(self, topic: str, msg: Message) -> None:
        self.sent.append((topic, msg))

    def targets(self) -> list[Target]:
        return [m for topic, m in self.sent if isinstance(m, Target)]

    def __enter__(self) -> "FakePub":
        return self

    def __exit__(self, *exc: object) -> None:
        pass


def _cfg_with_id_file(path: Path) -> Config:
    tracks = CFG.tracks.model_copy(update={"id_file": str(path)})
    return CFG.model_copy(update={"tracks": tracks})


def test_step_publishes_a_fresh_hit_and_drops_a_misstamped_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    state = new_state(_cfg_with_id_file(tmp_path / "ids"))
    now = time.time()
    script = iter(
        [
            # e.g. a module stamping t with time.monotonic(), or an un-retimed replay
            ("find.ble", ble(now - 3600).model_copy(update={"src": "ble_scanner"})),
            ("find.ble", ble(now + 3600)),
            ("find.ble", ble(now)),
        ]
    )
    sub, pub = FakeSub(script), FakePub()
    for _ in range(3):
        step(sub, pub, state)  # type: ignore[arg-type]
    [target] = pub.targets()
    assert target.priority == 3
    # The first is reported at once, naming its source; the next waits for the
    # summary, so a flood can't flood the log.
    [warning] = [r.getMessage() for r in caplog.records if r.name == "fuse.fusion"]
    assert "dropped 1" in warning and "ble_scanner" in warning
    assert state.skewed == 1


def test_track_ids_never_repeat_across_restarts(tmp_path: Path) -> None:
    cfg = _cfg_with_id_file(tmp_path / "ids")
    first = new_state(cfg)
    used = [first.tracker.add_rf(ble(0, dev=f"{i:016x}"), 0.1).id for i in range(250)]
    fusion._reserve_ids(first)
    second = new_state(cfg)  # a restart, or a power cut at this moment
    assert second.tracker.next_id > max(used)


def test_track_id_file_problems_never_stop_fusion(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert load_next_id(tmp_path / "missing") == 1
    garbled = tmp_path / "garbled"
    garbled.write_bytes(b"\xff\x00junk")
    # Unknown history: jump past any id a run could have used.
    assert load_next_id(garbled) > 1_000_000_000
    blocked = tmp_path / "file" / "ids"
    (tmp_path / "file").write_text("a file where a folder should be")
    save_next_id(blocked, 7)  # logs, doesn't raise
    assert "can't save track ids" in caplog.text


def test_a_crash_in_the_loop_keeps_tracks_and_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "sleep", lambda s: None)  # run_forever's back-off
    state = new_state(_cfg_with_id_file(tmp_path / "ids"))
    now = time.time()
    script = iter(
        [
            ("find.ble", ble(now)),
            RuntimeError("bus went away"),
            ("find.ble", ble(now, dev="2" * 16)),
        ]
    )
    pubs: list[FakePub] = []

    def new_pub(src: str) -> FakePub:
        pubs.append(FakePub())
        return pubs[-1]

    monkeypatch.setattr(fusion, "Subscriber", lambda topics: FakeSub(script))
    monkeypatch.setattr(fusion, "Publisher", new_pub)
    with pytest.raises(KeyboardInterrupt):
        run_forever("fusion", lambda: fusion.run(state))
    assert len(pubs) == 2  # restarted once
    ids = [t.id for p in pubs for t in p.targets()]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert len(state.tracker.tracks) == 2  # the first track survived the crash
    assert any(isinstance(m, Heartbeat) for p in pubs for _, m in p.sent)
