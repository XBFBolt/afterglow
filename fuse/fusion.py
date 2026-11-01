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

"Not moving" needs the radar to say so: unknown is not "still".

Send rules: a track's Target is published when the track is new, when its
priority changes, and every resend_s while it is P1 or P2.

Run with: python -m fuse.fusion
"""

import argparse
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

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
    Evidence,
    Hazard,
    HazardReading,
    Message,
    RadarHit,
    RfHit,
    Target,
    ThermalHit,
)
from common.service import run_forever
from find.rssi_filter import e_rf
from fuse.tracks import Track, Tracker, within

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
    """The track as a Target at time now. Bearing and range older than
    window_s are unknown (None); vitals and hazard are the last measured."""
    window_s = cfg.tracks.window_s
    evidence = Evidence(rf=track.e_rf, thermal=track.e_thermal, radar=track.e_radar)
    c = confidence(evidence, cfg.fusion.weights)
    radar = track.radar
    moving = radar.moving if radar else None
    recent_bearing = within(now, track.bearing_t, window_s)
    return Target(
        t=now,
        id=track.id,
        confidence=c,
        priority=triage(c, moving, track.hazard, cfg.triage),
        alive=track.alive,
        bearing_deg=track.bearing_deg if recent_bearing else None,
        range_m=_range_m(track, now, window_s),
        breaths_per_min=radar.breaths_per_min if radar else None,
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
    target = to_target(track, now, cfg)
    track.priority = target.priority
    if target.priority == track.sent_priority:
        return []
    return [_sent(track, target)]


def due(tracker: Tracker, now: float, cfg: Config) -> list[Target]:
    """Periodic resends: every P1 and P2 track not sent in the last resend_s.
    Elapsed time counts either way, so a clock set backwards can't stall them."""
    return [
        _sent(track, to_target(track, now, cfg))
        for track in tracker.tracks.values()
        if track.priority <= 2 and abs(now - track.sent_t) >= cfg.tracks.resend_s
    ]


def _apply(tracker: Tracker, msg: Message, cfg: Config) -> Track | None:
    """Hand a message to the tracker. A thermal hit with no person or a radar
    hit with nobody present is not evidence of absence (the sensors may just
    be pointed elsewhere), so it is skipped."""
    match msg:
        case RfHit():
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
    return target


# I/O below: track-id file, bus loop, entry point.


def load_next_id(path: Path) -> int:
    """The first track id this run may use. The command post keys markers by
    track id, so ids must never repeat, even after a crash or power cut."""
    try:
        return max(int(path.read_text()), 1)
    except FileNotFoundError:
        return 1
    except (OSError, ValueError) as exc:
        # Which ids were used is unknown: jump past any count a run could reach.
        log.error("can't read %s (%s); starting track ids from the clock", path, exc)
        return int(time.time())


def save_next_id(path: Path, next_id: int) -> None:
    """Record, atomically and durably, that ids below next_id may be in use.
    Logs a failure instead of raising: ids stay unique within this run."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w") as f:
            f.write(f"{next_id}\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        log.error(
            "can't save track ids to %s (%s): ids may repeat after a restart", path, exc
        )


@dataclass
class State:
    """What fusion keeps across loop restarts, so a crash in the loop neither
    forgets victims nor reuses track ids."""

    tracker: Tracker
    id_file: Path
    reserved_id: int = 0  # every id below this is saved as possibly in use
    skewed: int = 0  # messages dropped for a bad timestamp, not yet reported
    skewed_src: str = ""
    reported_evicted: int = 0
    last_report: float = -math.inf  # monotonic


def new_state(cfg: Config) -> State:
    id_file = REPO_ROOT / cfg.tracks.id_file
    tracker = Tracker(cfg.tracks, cfg.targets.devices, load_next_id(id_file))
    state = State(tracker, id_file)
    _reserve_ids(state)
    return state


def _reserve_ids(state: State) -> None:
    """Keep the saved id ahead of every id handed out."""
    if state.tracker.next_id >= state.reserved_id:
        state.reserved_id = state.tracker.next_id + ID_BLOCK
        save_next_id(state.id_file, state.reserved_id)


def step(sub: Subscriber, pub: Publisher, state: State) -> None:
    """One pass of the loop: take at most one message, publish what the send
    rules call for. Returns within health.beat_s."""
    cfg = get_config()
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
    for target in targets:
        pub.publish("fuse.target", target)
    _report(state)


def _report(state: State) -> None:
    """Summarize repeated problems at most once per bus.bad_msg_log_s, with counts."""
    evicted = state.tracker.evicted - state.reported_evicted
    now = time.monotonic()
    if not (state.skewed or evicted):
        return
    if now - state.last_report < get_config().bus.bad_msg_log_s:
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
    state.skewed = 0
    state.reported_evicted = state.tracker.evicted
    state.last_report = now


def run(state: State) -> None:
    """The fusion loop. Sockets open fresh on each (re)start; tracks in state
    survive a crash."""
    with Subscriber(INPUTS) as sub, Publisher("fusion") as pub:
        heart = Heart(pub, sim=get_config().sim)
        while True:
            step(sub, pub, state)
            heart.beat()


def main() -> None:
    argparse.ArgumentParser(description="Fuse sensor hits into targets.").parse_args()
    setup_logging()
    state = new_state(get_config())
    log.info("fusion up; next track id %d", state.tracker.next_id)
    run_forever("fusion", lambda: run(state))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
