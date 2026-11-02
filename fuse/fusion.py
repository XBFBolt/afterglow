"""Fusion: turn sensor hits into scored, triaged targets.

Subscribes to find.*, confirm.* and hazard.*, groups hits into tracks
(fuse/tracks.py), scores each track and publishes fuse.target when the send
rules call for it.

Score, a noisy-OR over the sensors with evidence on the track:

    C = 1 - Π (1 - w_i * e_i)

    e_rf       1 at or above rf.e_full_dbm, linear to 0 at rf.e_zero_dbm
    e_thermal  the detector's confidence
    e_radar    1 if a breathing rate in [breath_min, breath_max] is measured,
               else the module's confidence

Triage:

    P1  C >= confirmed and (radar says not moving, or hazard = danger)
    P2  C >= confirmed
    P3  C >= possible
    P4  below possible

"Not moving" needs the radar to say so: unknown is not "still". A track's
priority rises at once but drops only once the lower one has held for
demote_s, so a flickering sensor can't flip the marker every second.

Send rules: a track's Target is published when the track is new, when its
priority changes, when a breathing rate becomes known (the operator's proof
of life shouldn't wait for the next resend), and every resend_s while it is
P1 or P2.

With targets.known_only (demo and testing), devices not in targets.devices
are ignored, so bystanders' phones neither flood the link nor lend their
signal to a heat pack.

Run with: python -m fuse.fusion
"""

import argparse
import fcntl
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pydantic import TypeAdapter

from common.bus import Publisher, Subscriber
from common.config import (
    REPO_ROOT,
    Config,
    FusionConfig,
    TriageConfig,
    Weights,
    get_config,
    setup_logging,
)
from common.health import Heart
from common.schema import (
    BleHit,
    Evidence,
    Hazard,
    HazardReading,
    Message,
    RadarHit,
    Target,
    ThermalHit,
    WifiHit,
)
from common.service import run_forever
from find.rssi_filter import e_rf
from fuse.tracks import CONFIRMED, Track, Tracker, within

log = logging.getLogger(__name__)

INPUTS = ["find.", "confirm.", "hazard."]
ID_BLOCK = 100  # track ids reserved on disk at a time, so the file is written rarely


def confidence(evidence: Evidence, weights: Weights) -> float:
    """Noisy-OR of every sensor with evidence; a sensor with none (None) is left out."""
    miss = 1.0
    for sensor, e in evidence.model_dump().items():
        if e is not None:
            miss *= 1 - getattr(weights, sensor) * e
    return 1 - miss


def triage(
    c: float, moving: bool | None, hazard: Hazard | None, cfg: TriageConfig
) -> int:
    """Priority 1 (urgent) to 4 (device only)."""
    if c >= cfg.confirmed:
        return 1 if moving is False or hazard == "danger" else 2
    return 3 if c >= cfg.possible else 4


def radar_evidence(hit: RadarHit, cfg: FusionConfig) -> float:
    """e_radar: 1 for a plausible measured breathing rate, else the module's
    confidence."""
    rate = hit.breaths_per_min
    if rate is not None and cfg.breath_min <= rate <= cfg.breath_max:
        return 1.0
    return hit.conf


def to_target(track: Track, now: float, cfg: Config) -> Target:
    """The track as a Target at time now, with its priority as scored right
    now. A bearing older than bearing_s, and a range or breathing rate older
    than window_s, are unknown (None). Movement and hazard are the last
    measured, so a victim the searcher has left stays as urgent as they were."""
    window_s = cfg.tracks.window_s
    evidence = Evidence(rf=track.e_rf, thermal=track.e_thermal, radar=track.e_radar)
    c = confidence(evidence, cfg.fusion.weights)
    moving = track.radar.moving if track.radar else None
    recent_bearing = within(now, track.bearing_t, cfg.tracks.bearing_s)
    recent_breaths = within(now, track.breaths_t, window_s)
    return Target(
        t=now,
        id=track.id,
        confidence=c,
        priority=triage(c, moving, track.hazard, cfg.triage),
        alive=track.alive,
        bearing_deg=track.bearing_deg if recent_bearing else None,
        range_m=_range_m(track, now, window_s),
        breaths_per_min=track.breaths_per_min if recent_breaths else None,
        hazard=track.hazard,
        evidence=evidence,
    )


def _range_m(track: Track, now: float, window_s: float) -> float | None:
    """Radar range if recent (accurate), else the strongest recent device's
    estimate (rough), else unknown."""
    if track.radar is not None and within(now, track.radar.t, window_s):
        return track.radar.range_m
    recent = [hit for hit in track.rf.values() if within(now, hit.t, window_s)]
    if recent:
        return max(recent, key=lambda hit: hit.rssi_smooth).est_range_m
    return None


def on_message(tracker: Tracker, msg: Message, now: float, cfg: Config) -> list[Target]:
    """Feed one message to the tracker; return the Targets the send rules call for."""
    track = _apply(tracker, msg, cfg)
    if track is None:
        return []
    target = _refresh(track, now, cfg)
    news = target.priority != track.sent_priority or (
        target.breaths_per_min is not None and not track.sent_breathing
    )
    return [_sent(track, target)] if news else []


def due(tracker: Tracker, now: float, cfg: Config) -> list[Target]:
    """Targets owed on a timer: a drop in priority that has now held for
    demote_s, and every P1 and P2 not sent in the last resend_s. Elapsed time
    counts either way, so a clock set backwards can't stall them."""
    out = []
    for track in tracker.tracks.values():
        resend = track.confirmed and abs(now - track.sent_t) >= cfg.tracks.resend_s
        if not resend and track.demote_since is None:
            continue
        target = _refresh(track, now, cfg)
        if resend or target.priority != track.sent_priority:
            out.append(_sent(track, target))
    return out


def _refresh(track: Track, now: float, cfg: Config) -> Target:
    """The track's Target now, at the priority it reports: a more urgent one is
    taken at once, a less urgent one only after it has held for demote_s, so a
    flickering sensor can't flip the marker every second."""
    target = to_target(track, now, cfg)
    scored = target.priority
    if scored <= track.priority:
        track.priority, track.demote_since = scored, None
    elif track.demote_since is None:
        track.demote_since = now
    elif abs(now - track.demote_since) >= cfg.tracks.demote_s:
        track.priority, track.demote_since = scored, None
    return target.model_copy(update={"priority": track.priority})


def new_tracker(cfg: Config, next_id: int = 1) -> Tracker:
    return Tracker(
        cfg.tracks, cfg.targets.devices, next_id, known_only=cfg.targets.known_only
    )


def _apply(tracker: Tracker, msg: Message, cfg: Config) -> Track | None:
    """Hand a message to the tracker. A thermal hit with no person or a radar
    hit with nobody present is not evidence of absence (the sensors may just
    be pointed elsewhere), so it is skipped."""
    match msg:
        case WifiHit() | BleHit():
            return tracker.add_rf(msg, e_rf(msg.rssi_smooth, cfg.rf))
        case ThermalHit(person=True):
            return tracker.add_thermal(msg, msg.conf)
        case RadarHit(present=True):
            return tracker.add_radar(msg, radar_evidence(msg, cfg.fusion))
        case HazardReading():
            return tracker.add_gas(msg)
    return None


def _sent(track: Track, target: Target) -> Target:
    track.sent_priority = target.priority
    track.sent_t = target.t
    track.sent_breathing = target.breaths_per_min is not None
    return target


# I/O below: saved state, bus loop, entry point. Fusion's state lives in
# tracks.state_dir so it survives restarts and reboots: the next track id
# (the command post keys markers by id, so ids must never repeat) and every
# confirmed track (so P1s keep being resent after a power cut).

ID_EPOCH = 1_767_225_600  # 2026-01-01 00:00 UTC
TRACKS_JSON = TypeAdapter(list[Track])


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace path with data durably: after a power cut it holds the old data
    or the new, never a mix. Raises OSError."""
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    folder = os.open(path.parent, os.O_RDONLY)  # make the rename itself durable
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def load_next_id(path: Path) -> int:
    """The first track id this run may use: 1 on a fresh install."""
    try:
        return max(int(path.read_text()), 1)
    except FileNotFoundError:
        return 1
    except (OSError, ValueError) as exc:
        # Which ids were used is unknown. Minutes since 2026 only grow, and a
        # run would need over one new track a minute ever since to have passed
        # them; restored confirmed tracks push ids past their own as well. Six
        # digits, not ten, because every byte counts in a LoRa alert.
        log.error("can't read %s (%s); starting track ids from the clock", path, exc)
        return max((int(time.time()) - ID_EPOCH) // 60, 1)


def save_next_id(path: Path, next_id: int) -> None:
    """Record that ids below next_id may be in use. Logs a failure instead of
    raising: ids stay unique within this run."""
    try:
        _write_atomic(path, f"{next_id}\n".encode())
    except OSError as exc:
        log.error(
            "can't save track ids to %s (%s): ids may repeat after a restart", path, exc
        )


def load_tracks(path: Path) -> list[Track]:
    """Confirmed tracks saved by the last run; none if there is no file or it
    doesn't pass validation (logged), so a bad file can't stop fusion."""
    try:
        return TRACKS_JSON.validate_json(path.read_bytes())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        # The error type only: the detail could quote a garbled file at length.
        log.error(
            "can't load saved tracks from %s (%s); starting without them",
            path,
            type(exc).__name__,
        )
        return []


def save_tracks(path: Path, tracker: Tracker) -> None:
    """Save every P1/P2 track. Logs a failure instead of raising."""
    confirmed = [track for track in tracker.tracks.values() if track.confirmed]
    try:
        _write_atomic(path, TRACKS_JSON.dump_json(confirmed))
    except OSError as exc:
        log.error(
            "can't save tracks to %s (%s): a restart would forget them", path, exc
        )


@dataclass
class State:
    """What fusion keeps across loop restarts, so a crash in the loop neither
    forgets victims nor reuses track ids."""

    cfg: Config
    tracker: Tracker
    id_file: Path
    tracks_file: Path
    reserved_id: int = 0  # every id below this is saved as possibly in use
    skewed: int = 0  # messages dropped for a bad timestamp, not yet reported
    skewed_src: str = ""
    reported_evicted: int = 0
    reported_ignored: int = 0
    last_report: float = -math.inf  # monotonic


def _state_files(cfg: Config) -> tuple[Path, Path]:
    """(next track id file, saved tracks file)."""
    folder = REPO_ROOT / cfg.tracks.state_dir
    return folder / "next_track_id", folder / "tracks.json"


def new_state(cfg: Config) -> State:
    """Fusion's state, picking up the saved ids and the P1/P2 tracks seen in
    the last restore_s: a reboot mid-search keeps its victims, but last
    night's rehearsal doesn't come back."""
    id_file, tracks_file = _state_files(cfg)
    state = State(cfg, new_tracker(cfg, load_next_id(id_file)), id_file, tracks_file)
    now = time.time()
    saved = load_tracks(tracks_file)
    recent = [tr for tr in saved if within(now, tr.last_t, cfg.tracks.restore_s)]
    for track in recent:
        log.info(
            "restored track %d (P%d), last seen %.0f s ago",
            track.id,
            track.priority,
            now - track.last_t,
        )
    if len(recent) < len(saved):
        log.info(
            "left %d saved track(s) seen over restore_s ago", len(saved) - len(recent)
        )
    state.tracker.restore(recent)
    _reserve_ids(state)
    return state


def hold_lock(cfg: Config) -> TextIO | None:
    """Lock tracks.state_dir for this process; None if a running fusion holds
    it. Two fusions would hand out the same track ids, and a --new-search beside
    a running fusion would be undone by its next save. The OS drops the lock
    when the process ends, however it ends, so it can never go stale."""
    path = REPO_ROOT / cfg.tracks.state_dir / "fusion.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def new_search(cfg: Config) -> bool:
    """Forget saved tracks before a new search or rehearsal, keeping the id
    counter so no marker id is ever reused. Refuses (False) while fusion runs."""
    lock = hold_lock(cfg)
    if lock is None:
        log.error("fusion is still running: stop the node, then run --new-search")
        return False
    with lock:
        id_file, tracks_file = _state_files(cfg)
        count = len(load_tracks(tracks_file))
        tracks_file.unlink(missing_ok=True)
        log.info(
            "forgot %d saved track(s); track ids carry on from %d",
            count,
            load_next_id(id_file),
        )
    return True


def _reserve_ids(state: State) -> None:
    """Keep the saved id ahead of every id handed out."""
    if state.tracker.next_id >= state.reserved_id:
        state.reserved_id = state.tracker.next_id + ID_BLOCK
        save_next_id(state.id_file, state.reserved_id)


def step(sub: Subscriber, pub: Publisher, state: State) -> None:
    """One pass of the loop: take at most one message, publish what the send
    rules call for. Returns within health.beat_s."""
    cfg = state.cfg
    got = sub.recv(timeout_s=cfg.health.beat_s)
    now = time.time()
    targets: list[Target] = []
    if got is not None:
        _, msg = got
        if abs(msg.t - now) <= cfg.fusion.max_skew_s:
            targets += on_message(state.tracker, msg, now, cfg)
        else:
            state.skewed += 1
            state.skewed_src = msg.src
    targets += due(state.tracker, now, cfg)
    state.tracker.prune(now)
    _reserve_ids(state)  # before publishing, so no published id goes unsaved
    if any(target.priority <= CONFIRMED for target in targets):
        save_tracks(state.tracks_file, state.tracker)
    for target in targets:
        pub.publish("fuse.target", target)
    _report(state, now)


def _report(state: State, now: float) -> None:
    """Summarize repeated problems at most once per bus.bad_msg_log_s, with counts."""
    tracker = state.tracker
    evicted = tracker.evicted - state.reported_evicted
    ignored = tracker.ignored - state.reported_ignored
    mono = time.monotonic()
    if not (state.skewed or evicted or ignored):
        return
    if mono - state.last_report < state.cfg.bus.bad_msg_log_s:
        return
    if state.skewed:
        log.warning(
            "dropped %d message(s) stamped over max_skew_s from this clock; "
            "latest from %r (broken clock, or a replay that didn't retime)",
            state.skewed,
            state.skewed_src,
        )
    if evicted:
        log.warning("max_tracks reached: dropped %d least urgent track(s)", evicted)
    if ignored:
        _report_ignored(ignored, now - tracker.target_heard_t, state.cfg)
    state.skewed = 0
    state.reported_evicted = tracker.evicted
    state.reported_ignored = tracker.ignored
    state.last_report = mono


def _report_ignored(ignored: int, heard_ago_s: float, cfg: Config) -> None:
    """known_only drops every other device silently, so a target hash that no
    longer matches (changed salt, a phone randomizing its address) would blind
    the search without a word. Say how long since the target was heard."""
    if heard_ago_s <= cfg.tracks.window_s:
        log.info(
            "ignored %d hit(s) from non-target devices; target heard %.0f s ago",
            ignored,
            heard_ago_s,
        )
        return
    heard = "never" if math.isinf(heard_ago_s) else f"not for {heard_ago_s:.0f} s"
    log.warning(
        "ignored %d hit(s) from non-target devices, and target device heard %s: "
        "check targets.devices against tools.tap (salt changed? address randomized?)",
        ignored,
        heard,
    )


def run(state: State) -> None:
    """The fusion loop. Sockets open fresh on each (re)start; tracks in state
    survive a crash."""
    with Subscriber(INPUTS) as sub, Publisher("fusion") as pub:
        heart = Heart(pub, sim=state.cfg.sim)
        while True:
            step(sub, pub, state)
            heart.beat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fuse sensor hits into targets.")
    parser.add_argument(
        "--new-search",
        action="store_true",
        help="forget saved tracks (keeping the id counter) and exit; "
        "run with the node stopped, before each search or rehearsal",
    )
    args = parser.parse_args()
    setup_logging()
    cfg = get_config()
    if args.new_search:
        sys.exit(0 if new_search(cfg) else 1)
    lock = hold_lock(cfg)  # held for the life of the process
    if lock is None:
        log.error("another fusion is already running on this node; not starting")
        sys.exit(1)
    state = new_state(cfg)
    log.info(
        "fusion up; %d track(s) restored; next track id %d",
        len(state.tracker.tracks),
        state.tracker.next_id,
    )
    run_forever("fusion", lambda: run(state))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
