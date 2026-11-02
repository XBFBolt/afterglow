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
    new_search,
    new_state,
    on_message,
    radar_evidence,
    save_next_id,
    save_tracks,
    step,
    to_target,
    triage,
)
from fuse.tracks import Tracker
from tools import fake_detections as fake

W = Weights(rf=0.5, thermal=0.7, radar=0.7, audio=0.4)
TRIAGE = TriageConfig(confirmed=0.8, possible=0.5)
CFG = get_config()  # shipped: known_only, so only target devices count
KNOWN = CFG.targets.devices[0]
STRANGER = "b" * 16  # a device that isn't a target


def _cfg(state_dir: Path | None = None, known_only: bool | None = None) -> Config:
    cfg = CFG
    if state_dir is not None:
        tracks = cfg.tracks.model_copy(update={"state_dir": str(state_dir)})
        cfg = cfg.model_copy(update={"tracks": tracks})
    if known_only is not None:
        targets = cfg.targets.model_copy(update={"known_only": known_only})
        cfg = cfg.model_copy(update={"targets": targets})
    return cfg


OPEN = _cfg(known_only=False)  # a real search: every device counts


def _tracker(cfg: Config = CFG) -> Tracker:
    return fusion.new_tracker(cfg)


def ble(t: float, rssi: float = -55, rng: float = 3.0, dev: str = KNOWN) -> BleHit:
    return BleHit(t=t, dev=dev, rssi_dbm=rssi, rssi_smooth=rssi, est_range_m=rng)


def thermal(
    t: float, conf: float = 0.9, person: bool = True, bearing: float = 40
) -> ThermalHit:
    return ThermalHit(t=t, person=person, conf=conf, bearing_deg=bearing, max_temp_c=34)


def radar(
    t: float,
    moving: bool = False,
    breaths: float | None = None,
    conf: float = 0.8,
    range_m: float = 2.0,
) -> RadarHit:
    return RadarHit(
        t=t,
        present=True,
        moving=moving,
        range_m=range_m,
        breaths_per_min=breaths,
        conf=conf,
    )


def _feed(tracker: Tracker, msgs: list[Message], cfg: Config = CFG) -> list[Target]:
    sent: list[Target] = []
    for msg in msgs:
        sent += on_message(tracker, msg, msg.t, cfg)
    return sent


def _play(tracker: Tracker, msgs: list[Message], cfg: Config = CFG) -> list[Target]:
    """Like the loop: each message, then whatever is due."""
    sent: list[Target] = []
    for msg in msgs:
        sent += on_message(tracker, msg, msg.t, cfg) + due(tracker, msg.t, cfg)
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


def test_a_heat_pack_stays_below_confirmed_beside_the_searchers_own_phone() -> None:
    # Review probe 1: the searcher's phone lent its signal to a heat pack (P2).
    own_phone = ble(0, rssi=-40, rng=0.5, dev=STRANGER)  # in the searcher's pocket
    sent = _feed(_tracker(), [own_phone, thermal(1, conf=0.95)])
    assert max(t.confidence for t in sent) < CFG.triage.confirmed


def test_phone_on_a_table_with_no_person_stays_p3_or_p4() -> None:
    sent = _feed(_tracker(), [ble(t, rssi=-40, rng=0.5) for t in range(100)])
    assert {t.priority for t in sent} <= {3, 4}
    assert not any(t.alive for t in sent)


def test_thermal_and_radar_misses_are_not_evidence_of_absence() -> None:
    tracker = _tracker()
    empty_room = RadarHit(t=1, present=False, conf=0.05)
    sent = _feed(tracker, [thermal(0, person=False), empty_room])
    assert sent == [] and tracker.tracks == {}


# Bystanders' devices (targets.known_only)


def test_rotating_macs_cannot_flood_the_link() -> None:
    # Review probe 1: 10 new MACs a second gave 600 targets a minute.
    tracker = _tracker()
    flood = [ble(i / 10, dev=f"{i:016x}") for i in range(600)]
    assert _feed(tracker, flood) == []
    assert tracker.tracks == {} and tracker.evicted == 0


def test_a_person_never_borrows_a_bystanders_phone() -> None:
    # Review probe 6: the victim lies on their phone, so it reads 9 m, past
    # confirm_range_m; the person then joined a bystander's phone.
    tracker = _tracker()
    on_victim = ble(0, rssi=-73, rng=9.0)
    bystander = ble(0, rssi=-60, rng=3.0, dev=STRANGER)
    _feed(tracker, [on_victim, bystander, thermal(1)])
    person = next(tr for tr in tracker.tracks.values() if tr.alive)
    assert STRANGER not in person.rf


def test_in_a_real_search_every_device_counts_but_alone_is_only_p4() -> None:
    # A device alone scores at most 0.5, just under triage.possible: "device
    # only", so passing phones don't each become a P3 alert.
    tracker = _tracker(OPEN)
    [target] = _feed(tracker, [ble(0, rssi=-40, dev=STRANGER)], OPEN)
    assert target.confidence == 0.5 and target.priority == 4


# Send rules and priority


def test_send_on_new_track_and_on_priority_change_only() -> None:
    tracker = _tracker()
    sent = _feed(tracker, [ble(0), ble(1), ble(2)])  # P4, no change
    assert [t.priority for t in sent] == [4]
    sent = _feed(tracker, [thermal(3)])  # rf + thermal: confirmed, movement unknown
    assert [t.priority for t in sent] == [2]
    sent = _feed(tracker, [radar(4, moving=False)])
    assert [t.priority for t in sent] == [1]


def test_a_flickering_moving_flag_does_not_flip_priority() -> None:
    # Review probe 3: an LD2410 flickering at 1 Hz sent 60 targets a minute.
    msgs: list[Message] = [ble(0), thermal(0.5)]
    for i in range(60):
        msgs += [thermal(1 + i), radar(1 + i, moving=i % 2 == 1)]
    priorities = [t.priority for t in _play(_tracker(), msgs)]
    assert priorities[:3] == [4, 2, 1]
    assert set(priorities[3:]) == {1}  # resends only
    assert len(priorities) <= 3 + 60 // CFG.tracks.resend_s


def test_sustained_movement_demotes_after_demote_s_even_with_no_new_hits() -> None:
    tracker = _tracker()
    _feed(tracker, [ble(0), thermal(1), radar(2, moving=False)])  # P1
    assert _feed(tracker, [radar(3, moving=True)]) == []  # held at P1
    demote = CFG.tracks.demote_s
    assert due(tracker, 3 + demote - 0.1, CFG) == []
    [target] = due(tracker, 3 + demote, CFG)
    assert target.priority == 2


def test_a_rise_in_priority_is_never_held_back() -> None:
    tracker = _tracker()
    _feed(tracker, [ble(0), thermal(1), radar(2, moving=True)])  # P2
    [target] = _feed(tracker, [radar(3, moving=False)])
    assert target.priority == 1


def test_danger_makes_a_moving_confirmed_person_p1() -> None:
    tracker = _tracker()
    _feed(tracker, [ble(0), thermal(1), radar(2, moving=True)])
    hazard = HazardReading(t=3, hazard="danger", gas_raw=900)
    [target] = on_message(tracker, hazard, 3, CFG)
    assert target.priority == 1 and target.hazard == "danger"


def test_p1_and_p2_resend_every_resend_s_and_p3_p4_never() -> None:
    tracker = _tracker(OPEN)
    _feed(tracker, [ble(0), thermal(1), radar(2)], OPEN)  # P1
    _feed(tracker, [ble(3, dev=STRANGER, rssi=-85, rng=20)], OPEN)  # P4
    resend = CFG.tracks.resend_s
    assert due(tracker, 2 + resend - 0.1, OPEN) == []
    [target] = due(tracker, 2 + resend, OPEN)
    assert target.priority == 1
    assert due(tracker, 2 + resend + 1, OPEN) == []  # just sent
    assert len(due(tracker, 2 + 2 * resend, OPEN)) == 1


def test_a_victim_is_not_downgraded_when_the_radar_sees_someone_else() -> None:
    # Review probe 2: walking away, the radar saw someone moving at 6 m; that
    # landed on the victim, who dropped to P2 with breathing unknown.
    tracker = _tracker()
    _feed(tracker, [ble(0), thermal(1), radar(2, breaths=14)])  # P1, breathing
    sent = _feed(tracker, [radar(3, moving=True, range_m=6.0)])
    victim, other = tracker.tracks.values()
    assert [t.id for t in sent] == [other.id]
    target = to_target(victim, 4, CFG)
    assert target.priority == 1 and target.breaths_per_min == 14


# What a target reports as time passes


def test_stale_readings_become_unknown_but_the_person_stays_urgent() -> None:
    tracker = _tracker()
    _feed(tracker, [ble(0), thermal(1), radar(2, breaths=14)])
    [track] = tracker.tracks.values()
    fresh = to_target(track, 3, CFG)
    assert fresh.bearing_deg == 40 and fresh.range_m == 2.0
    assert fresh.breaths_per_min == 14
    later = to_target(track, 600, CFG)  # searcher left ten minutes ago
    assert later.bearing_deg is None and later.range_m is None
    assert later.breaths_per_min is None  # old vitals don't pass as current
    assert later.priority == 1 and later.alive  # the last radar reading still counts
    assert later.confidence == fresh.confidence


def test_breathing_is_the_newest_measured_rate() -> None:
    tracker = _tracker()
    _feed(tracker, [ble(0), thermal(1), radar(2, breaths=14), radar(3, breaths=None)])
    [track] = tracker.tracks.values()
    assert to_target(track, 4, CFG).breaths_per_min == 14


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


def _scenario(
    cfg: Config, tracker: Tracker | None = None, start: float = 0.0
) -> tuple[Tracker, list[Target]]:
    """Play the fake scenario through fusion's logic, starting at `start`."""
    tracker = tracker or _tracker(cfg)
    sent: list[Target] = []
    for _, msg in fake.script(random.Random(7)):
        msg = msg.model_copy(update={"t": start + msg.t})
        sent += on_message(tracker, msg, msg.t, cfg) + due(tracker, msg.t, cfg)
        tracker.prune(msg.t)
    return tracker, sent


def test_fake_scenario_climbs_to_p1() -> None:
    tracker, sent = _scenario(CFG)
    victim_dev = hash_device(fake.VICTIM_MAC)
    [victim] = tracker.tracks.values()  # the bystander's phone is ignored
    assert victim_dev in victim.rf
    priorities = [t.priority for t in sent]
    # Starts as a faint device, only ever gets more urgent, ends urgent.
    assert priorities[0] == 4 and priorities[-1] == 1
    assert priorities == sorted(priorities, reverse=True)
    assert 3 in priorities
    # Confirmed only once heat appears; P1 only once radar says still.
    assert all(t.t >= fake.THERMAL_FROM_S for t in sent if t.priority <= 2)
    assert all(t.t >= fake.RADAR_FROM_S for t in sent if t.priority == 1)
    last = sent[-1]
    assert last.alive and last.hazard == "caution"
    assert last.breaths_per_min is not None and 13 <= last.breaths_per_min <= 15
    assert last.bearing_deg is not None
    assert abs(last.bearing_deg - fake.VICTIM_BEARING_DEG) <= 5


def test_fake_scenario_in_a_real_search_keeps_the_bystander_at_p4() -> None:
    tracker, sent = _scenario(OPEN)
    victim_dev = hash_device(fake.VICTIM_MAC)
    [victim] = [tr for tr in tracker.tracks.values() if victim_dev in tr.rf]
    assert victim.priority == 1 and len(tracker.tracks) == 2
    assert {t.priority for t in sent if t.id != victim.id} == {4}


# The loop: timestamps, saved state, crashes


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


def _steps(state: fusion.State, msgs: list[Message]) -> FakePub:
    """Run msgs through step() as the loop would."""
    sub, pub = FakeSub(iter([("bus", msg) for msg in msgs])), FakePub()
    for _ in msgs:
        step(sub, pub, state)  # type: ignore[arg-type]
    return pub


def test_step_publishes_a_fresh_hit_and_drops_a_misstamped_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    state = new_state(_cfg(tmp_path))
    now = time.time()
    pub = _steps(
        state,
        [
            # e.g. a module stamping t with time.monotonic(), or an un-retimed replay
            ble(now - 3600).model_copy(update={"src": "ble_scanner"}),
            ble(now + 3600),
            ble(now),
        ],
    )
    [target] = pub.targets()
    assert target.priority == 4
    # The first is reported at once, naming its source; the next waits for the
    # summary, so a flood can't flood the log.
    [warning] = [r.getMessage() for r in caplog.records if r.name == "fuse.fusion"]
    assert "dropped 1" in warning and "ble_scanner" in warning
    assert state.skewed == 1


def test_track_ids_never_repeat_across_restarts(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, known_only=False)
    first = new_state(cfg)
    used = [first.tracker.next_id]
    for i in range(250):
        first.tracker.add_rf(ble(0, dev=f"{i:016x}"), 0.1)
        used.append(first.tracker.next_id - 1)
    fusion._reserve_ids(first)
    second = new_state(cfg)  # a restart, or a power cut at this moment
    assert second.tracker.next_id > max(used)


def test_confirmed_tracks_survive_a_restart(tmp_path: Path) -> None:
    # Review: a reboot started empty, so P1s stopped being resent.
    cfg = _cfg(tmp_path, known_only=False)
    state = new_state(cfg)
    now = time.time()
    far_phone = ble(now, rssi=-85, rng=20, dev=STRANGER)
    _steps(state, [ble(now), thermal(now), radar(now, breaths=14), far_phone])
    victim_id = next(tr.id for tr in state.tracker.tracks.values() if tr.alive)
    restarted = new_state(cfg)
    [track] = restarted.tracker.tracks.values()  # device-only tracks aren't kept
    assert track.id == victim_id and track.priority == 1
    assert track.breaths_per_min == 14
    assert restarted.tracker.next_id > max(state.tracker.tracks)
    [resent] = due(restarted.tracker, now + cfg.tracks.resend_s + 1, cfg)
    assert resent.id == victim_id and resent.priority == 1


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"[{",  # cut short
        b'[{"id": 1}]',  # missing fields
        b'[{"id": 1, "last_t": NaN}]',
        b'[{"id": 1, "last_t": 0, "e_rf": 5}]',  # evidence out of range
        b'[{"id": 1, "last_t": 0, "priority": 9}]',
        b'[{"id": 1, "last_t": 0, "mystery": 1}]',
    ],
)
def test_a_bad_tracks_file_never_stops_fusion(
    content: bytes, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "tracks.json").write_bytes(content)
    state = new_state(_cfg(tmp_path))
    assert state.tracker.tracks == {}
    assert "can't load saved tracks" in caplog.text


def test_track_id_file_problems_never_stop_fusion(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert load_next_id(tmp_path / "missing") == 1
    garbled = tmp_path / "garbled"
    garbled.write_bytes(b"\xff\x00junk")
    # Unknown history: jump past any id a run could have used, in few digits
    # (every byte counts in a LoRa alert).
    minutes = (int(time.time()) - fusion.ID_EPOCH) // 60
    assert minutes <= load_next_id(garbled) < 10_000_000
    blocked = tmp_path / "file" / "ids"
    (tmp_path / "file").write_text("a file where a folder should be")
    save_next_id(blocked, 7)  # logs, doesn't raise
    assert "can't save track ids" in caplog.text


def test_a_crash_in_the_loop_keeps_tracks_and_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "sleep", lambda s: None)  # run_forever's back-off
    state = new_state(_cfg(tmp_path, known_only=False))
    now = time.time()
    script = iter(
        [
            ("find.ble", ble(now)),
            RuntimeError("bus went away"),
            ("find.ble", ble(now, dev=STRANGER)),
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


# Regressions from the second fusion review


def _save(state: fusion.State) -> None:
    save_tracks(state.tracks_file, state.tracker)
    fusion._reserve_ids(state)


def test_last_nights_rehearsal_does_not_come_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Probe 1: a saved P1 from an earlier run was resent at startup, took this
    # run's phone, and this run's victim became a second P1 with no phone.
    caplog.set_level("INFO")
    cfg = _cfg(tmp_path)
    night = new_state(cfg)
    _scenario(cfg, night.tracker, start=time.time() - 12 * 3600)
    _save(night)
    today = new_state(cfg)
    assert today.tracker.tracks == {}
    assert "left 1 saved track(s) seen over restore_s ago" in caplog.text
    tracker, sent = _scenario(cfg, today.tracker, start=time.time())
    assert sent[0].priority == 4 and sent[-1].priority == 1
    assert len(tracker.tracks) == 1 and sent[0].id >= night.tracker.next_id


def test_a_reboot_mid_search_keeps_its_victims_and_says_how_old(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    cfg = _cfg(tmp_path)
    before = new_state(cfg)
    _scenario(cfg, before.tracker, start=time.time() - 180)
    _save(before)
    [track] = new_state(cfg).tracker.tracks.values()
    assert track.priority == 1
    assert f"restored track {track.id} (P1), last seen" in caplog.text


def test_new_search_forgets_a_recent_rehearsal_but_never_reuses_its_ids(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)
    rehearsal = new_state(cfg)
    _, first = _scenario(cfg, rehearsal.tracker, start=time.time() - 300)
    _save(rehearsal)
    new_search(cfg)
    demo = new_state(cfg)
    assert demo.tracker.tracks == {}
    _, second = _scenario(cfg, demo.tracker, start=time.time())
    assert second[0].priority == 4 and second[-1].priority == 1
    assert {t.id for t in first}.isdisjoint(t.id for t in second)


def test_one_fusion_at_a_time_and_new_search_waits_for_it_to_stop(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Found in the live check: an orphaned fusion kept resending the
    # rehearsal's P1 after --new-search, and two fusions would share one id
    # counter.
    cfg = _cfg(tmp_path)
    running = fusion.hold_lock(cfg)
    assert running is not None
    assert fusion.hold_lock(cfg) is None  # a second fusion won't start
    assert new_search(cfg) is False
    assert "stop the node" in caplog.text
    running.close()  # the node stops
    assert new_search(cfg) is True


def test_a_crowd_does_not_pile_up_or_come_back(tmp_path: Path) -> None:
    # Probe 3: ten minutes through a crowd left 99 tracks kept for good and
    # saved for the next start, because "seen once" counted as confirmed.
    cfg = _cfg(tmp_path, known_only=False)
    state = new_state(cfg)
    rng = random.Random(1)
    start = time.time() - 1300
    msgs: list[Message] = []
    for i in range(300):  # 10 min; someone in frame 30% of the time, no phones
        t = start + 2 * i
        if rng.random() < 0.3:
            msgs.append(thermal(t, bearing=rng.uniform(0, 359)))
            if rng.random() < 0.5:
                msgs.append(radar(t + 0.1, moving=True, range_m=rng.uniform(1, 8)))
    _play(state.tracker, msgs, cfg)
    created = state.tracker.next_id - 1
    state.tracker.prune(msgs[-1].t + cfg.tracks.keep_s + 1)
    kept = list(state.tracker.tracks.values())
    assert len(kept) < created
    assert all(track.confirmed for track in kept)  # only P1/P2 stay for good
    _save(state)
    restored = new_state(cfg).tracker.tracks
    assert {tr.id for tr in kept} >= set(restored)


def test_known_only_warns_when_the_target_device_is_never_heard(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Review: a hash that no longer matches (changed salt, randomized address)
    # blinded the device search without a word.
    state = new_state(_cfg(tmp_path))
    now = time.time()
    _steps(state, [ble(now, dev=STRANGER), ble(now, dev="c" * 16)])
    [warning] = [r for r in caplog.records if r.name == "fuse.fusion"]
    assert warning.levelname == "WARNING"
    assert "ignored 1 hit(s)" in warning.getMessage()
    assert "target device heard never" in warning.getMessage()


def test_known_only_reports_quietly_while_the_target_is_heard(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    state = new_state(_cfg(tmp_path))
    now = time.time()
    _steps(state, [ble(now), ble(now, dev=STRANGER)])
    [record] = [r for r in caplog.records if "ignored" in r.getMessage()]
    assert record.levelname == "INFO"
    assert "target heard 0 s ago" in record.getMessage()
