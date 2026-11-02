"""Group sensor hits into target tracks: one track per probable person.

Pure logic, no I/O. Fusion works out each hit's evidence e and hands it in;
the tracker decides which track the hit joins and keeps each track's state.

Which track a hit joins (windows and limits from config.yaml, tracks section):

0. With targets.known_only, a device hit from anything but a target device is
   ignored (and counted, so a target that is never heard can be noticed).
1. A device hit (Wi-Fi, BLE) joins the track that already holds its device hash.
2. A hit with a bearing joins the track seen at the nearest bearing within
   bearing_merge_deg, if that bearing is under window_s old.
3. The thermal camera and radar share a bracket and look at one thing at a
   time. Radar gives no bearing but does give range, so a radar hit joins the
   track the radar saw under focus_s ago at the nearest range within
   radar_merge_m, plus radar_drift_mps for each second since that reading (the
   searcher walks). Failing that, it joins the track the camera or radar last
   saw (the focus, under focus_s ago) if the radar has nothing recent on it:
   someone at another range is someone else. A thermal hit joins the focus
   too, if the focus has no recent bearing.
4. When they find someone not yet tracked, that person joins the device track
   the searcher was closing in on: a device heard in the last window_s and
   estimated within confirm_range_m, on a track nobody has been seen on.
   Known target devices come first, then the strongest signal. Two people must
   never share one marker; a duplicate marker is the lesser evil.
5. Anything else starts a new track.
6. Whenever a person on a track with no device is seen, they take the device
   track rule 4 would pick, and that track's id is retired. A person first
   seen from too far away still gets their phone as the searcher closes in,
   and since the device track has nobody on it, no second person is hidden.

Each track keeps the strongest evidence each sensor has given it, so confidence
never drops because the searcher walked away or looked elsewhere: a person
confirmed ten minutes ago is still there. Bearing, range and breathing rate go
stale, so fusion reports them only while under window_s old. "Recent" means
within that many seconds either way (see within()), so a clock set backwards
mid-search can't make old readings look fresh.

Only P1/P2 tracks (C >= triage.confirmed) are kept for good. Anything below is
forgotten after keep_s unheard, so a crowd seen once doesn't pile up. Each
track remembers at most max_devs device hashes, and past max_tracks one is
dropped: below P2 first, then unknown devices before known targets, then the
least urgent, then the longest quiet.
"""

import math
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Annotated

from pydantic import ConfigDict, Field

from common.config import TracksConfig
from common.schema import (
    T_MAX,
    Bearing,
    BleHit,
    BreathRate,
    Hazard,
    HazardReading,
    Priority,
    RadarHit,
    Score,
    ThermalHit,
    WifiHit,
)

# A time, or -inf for "never". NaN fails both bounds, so a garbled saved track
# can't stall resends with a time that compares false to everything.
Time = Annotated[float, Field(ge=-math.inf, le=T_MAX)]
CONFIRMED = 2  # P1 and P2: C >= triage.confirmed


@dataclass(eq=False)
class Track:
    """One probable person. Fusion reads it to build a Target and saves the
    P1/P2 ones so they survive a restart. The annotations are checked when a
    saved track is loaded (pydantic), not when the tracker updates it."""

    __pydantic_config__ = ConfigDict(ser_json_inf_nan="constants", extra="forbid")

    id: Annotated[int, Field(ge=0)]
    last_t: Time  # newest hit of any kind
    rf: dict[str, WifiHit | BleHit] = field(default_factory=dict)  # newest per device
    bearing_deg: Bearing | None = None
    bearing_t: Time = -math.inf  # when bearing_deg was measured
    confirm_t: Time = -math.inf  # newest thermal or radar hit
    radar: RadarHit | None = None  # newest radar hit that saw someone
    breaths_per_min: BreathRate | None = None  # newest measured breathing rate
    breaths_t: Time = -math.inf
    hazard: Hazard | None = None  # gas reading taken at this track; None: none taken
    # Strongest evidence so far from each sensor; None: nothing from it yet.
    e_rf: Score | None = None
    e_thermal: Score | None = None
    e_radar: Score | None = None
    # Kept by fusion: the priority it reports, when a lower one was first
    # scored (it must hold for demote_s), and what it last published and when.
    priority: Priority = 4
    demote_since: Time | None = None
    sent_priority: Priority | None = None
    sent_t: Time = -math.inf

    @property
    def alive(self) -> bool:
        """Thermal or radar has seen someone on this track, if only once."""
        return self.e_thermal is not None or self.e_radar is not None

    @property
    def confirmed(self) -> bool:
        """P1 or P2: kept for good, saved across restarts, evicted last."""
        return self.priority <= CONFIRMED


def angle_between(a: float, b: float) -> float:
    """Smallest angle between two bearings, 0 to 180 degrees."""
    return abs((a - b + 180) % 360 - 180)


def within(t: float, then: float, seconds: float) -> bool:
    """t and then are no more than `seconds` apart, either way. Either way, so
    a clock set backwards mid-search can't make old readings look fresh."""
    return abs(t - then) <= seconds


def _peak(old: float | None, new: float) -> float:
    return new if old is None else max(old, new)


class Tracker:
    """Every live track, and the rules for which one a hit joins."""

    def __init__(
        self,
        cfg: TracksConfig,
        known: Collection[str],
        next_id: int = 1,
        known_only: bool = False,
    ) -> None:
        self.cfg = cfg
        self.known = frozenset(known)  # hashes of target devices from config
        self.known_only = known_only  # rule 0
        self.tracks: dict[int, Track] = {}
        self.next_id = next_id
        self.evicted = 0  # tracks dropped because max_tracks was reached
        self.ignored = 0  # device hits dropped by rule 0
        self.target_heard_t = -math.inf  # newest hit from a target device
        self._gas: HazardReading | None = None  # newest gas reading

    def add_rf(self, hit: WifiHit | BleHit, e: float) -> Track | None:
        """A Wi-Fi or BLE hit, with its e_rf. None if rule 0 ignores it."""
        if hit.dev in self.known:
            self.target_heard_t = max(self.target_heard_t, hit.t)
        elif self.known_only:
            self.ignored += 1
            return None
        track = self._by_dev(hit.dev)
        if track is None and hit.bearing_deg is not None:
            track = self._by_bearing(hit.bearing_deg, hit.t)
        track = track or self._new(hit.t)
        self._add_dev(track, hit)
        track.last_t = max(track.last_t, hit.t)
        if hit.bearing_deg is not None:
            self._see_bearing(track, hit.bearing_deg, hit.t)
        track.e_rf = _peak(track.e_rf, e)
        return track

    def add_thermal(self, hit: ThermalHit, e: float) -> Track:
        """A thermal hit that saw a person, with its e."""
        track = (
            self._by_bearing(hit.bearing_deg, hit.t)
            or self._focus_without_bearing(hit.t)
            or self._homing(hit.t)
            or self._new(hit.t)
        )
        self._confirm(track, hit.t)
        self._see_bearing(track, hit.bearing_deg, hit.t)
        track.e_thermal = _peak(track.e_thermal, e)
        return track

    def add_radar(self, hit: RadarHit, e: float) -> Track:
        """A radar hit that saw someone present, with its e."""
        track = (
            self._by_radar_range(hit)
            or self._focus_without_radar(hit.t)
            or self._homing(hit.t)
            or self._new(hit.t)
        )
        self._confirm(track, hit.t)
        if track.radar is None or hit.t >= track.radar.t:
            track.radar = hit
        if hit.breaths_per_min is not None and hit.t >= track.breaths_t:
            track.breaths_per_min, track.breaths_t = hit.breaths_per_min, hit.t
        track.e_radar = _peak(track.e_radar, e)
        return track

    def add_gas(self, reading: HazardReading) -> Track | None:
        """Gas is measured where the searcher stands, so it describes the track
        they are at (the focus), if any. Returns that track."""
        self._gas = reading
        track = self._focus(reading.t)
        if track is not None:
            track.hazard = reading.hazard
        return track

    def prune(self, now: float) -> None:
        """Forget tracks below P2 unheard for keep_s. P1/P2 tracks stay until
        max_tracks pushes them out."""
        stale = [
            track.id
            for track in self.tracks.values()
            if not track.confirmed and now - track.last_t > self.cfg.keep_s
        ]
        for track_id in stale:
            del self.tracks[track_id]

    def restore(self, tracks: Collection[Track]) -> None:
        """Take back tracks saved before a restart, most urgent first, up to
        max_tracks. New ids carry on past every restored one."""
        ranked = sorted(tracks, key=lambda tr: (tr.priority, -tr.last_t))
        for track in ranked[: self.cfg.max_tracks]:
            self.tracks[track.id] = track
            self.next_id = max(self.next_id, track.id + 1)

    def _new(self, t: float) -> Track:
        if len(self.tracks) >= self.cfg.max_tracks:
            drop = max(self.tracks.values(), key=self._expendable)
            del self.tracks[drop.id]
            self.evicted += 1
        track = Track(id=self.next_id, last_t=t)
        self.next_id += 1
        self.tracks[track.id] = track
        return track

    def _expendable(self, track: Track) -> tuple[bool, bool, int, float]:
        """Sort key; the highest is dropped first when max_tracks is reached."""
        known = not self.known.isdisjoint(track.rf)
        return (not track.confirmed, not known, track.priority, -track.last_t)

    def _add_dev(self, track: Track, hit: WifiHit | BleHit) -> None:
        track.rf.pop(hit.dev, None)  # re-insert, so the dict runs oldest to newest
        track.rf[hit.dev] = hit
        if len(track.rf) > self.cfg.max_devs:
            del track.rf[next(iter(track.rf))]

    def _absorb(self, person: Track, device: Track) -> None:
        """Fold a device-only track into a person's track (rule 6)."""
        for hit in device.rf.values():
            self._add_dev(person, hit)
        if device.e_rf is not None:
            person.e_rf = _peak(person.e_rf, device.e_rf)
        person.last_t = max(person.last_t, device.last_t)
        del self.tracks[device.id]

    def _by_dev(self, dev: str) -> Track | None:
        return next((tr for tr in self.tracks.values() if dev in tr.rf), None)

    def _has_bearing(self, track: Track, t: float) -> bool:
        """Track has a bearing under window_s old."""
        recent = within(t, track.bearing_t, self.cfg.window_s)
        return track.bearing_deg is not None and recent

    def _by_bearing(self, bearing: float, t: float) -> Track | None:
        """The track at the nearest recent bearing within bearing_merge_deg."""
        best, best_angle = None, self.cfg.bearing_merge_deg
        for track in self.tracks.values():
            if track.bearing_deg is None or not self._has_bearing(track, t):
                continue
            angle = angle_between(bearing, track.bearing_deg)
            if angle <= best_angle:
                best, best_angle = track, angle
        return best

    def _focus(self, t: float) -> Track | None:
        """The track thermal or radar saw last, if under focus_s ago."""
        track = max(self.tracks.values(), key=lambda tr: tr.confirm_t, default=None)
        if track is not None and within(t, track.confirm_t, self.cfg.focus_s):
            return track
        return None

    def _focus_without_bearing(self, t: float) -> Track | None:
        track = self._focus(t)
        return None if track is None or self._has_bearing(track, t) else track

    def _has_radar(self, track: Track, t: float) -> bool:
        """The radar saw someone on track under focus_s ago."""
        radar = track.radar
        return radar is not None and within(t, radar.t, self.cfg.focus_s)

    def _by_radar_range(self, hit: RadarHit) -> Track | None:
        """The track the radar saw recently at the nearest range within
        radar_merge_m, widened by radar_drift_mps per second since (rule 3)."""
        best, best_gap = None, math.inf
        for track in self.tracks.values():
            if track.radar is None or not self._has_radar(track, hit.t):
                continue
            if hit.range_m is None or track.radar.range_m is None:
                continue  # can't happen for a present hit; the schema requires range
            gap = abs(hit.range_m - track.radar.range_m)
            since_s = abs(hit.t - track.radar.t)
            allowed = self.cfg.radar_merge_m + self.cfg.radar_drift_mps * since_s
            if gap <= allowed and gap < best_gap:
                best, best_gap = track, gap
        return best

    def _focus_without_radar(self, t: float) -> Track | None:
        track = self._focus(t)
        return None if track is None or self._has_radar(track, t) else track

    def _homing(self, t: float) -> Track | None:
        """The device track the searcher was closing in on (rule 4)."""
        best, best_key = None, None
        for track in self.tracks.values():
            if track.alive or self._has_bearing(track, t):
                continue
            near = [
                hit
                for hit in track.rf.values()
                if within(t, hit.t, self.cfg.window_s)
                and hit.est_range_m <= self.cfg.confirm_range_m
            ]
            if not near:
                continue
            key = (
                any(hit.dev in self.known for hit in near),
                max(hit.rssi_smooth for hit in near),
            )
            if best_key is None or key > best_key:
                best, best_key = track, key
        return best

    def _confirm(self, track: Track, t: float) -> None:
        """Thermal or radar saw someone on track at time t."""
        track.confirm_t = max(track.confirm_t, t)
        track.last_t = max(track.last_t, t)
        if not track.rf and (device := self._homing(t)) is not None:
            self._absorb(track, device)  # rule 6
        gas = self._gas
        if gas is not None and within(t, gas.t, self.cfg.focus_s):
            track.hazard = gas.hazard

    def _see_bearing(self, track: Track, bearing: float, t: float) -> None:
        if t >= track.bearing_t:
            track.bearing_deg, track.bearing_t = bearing, t
