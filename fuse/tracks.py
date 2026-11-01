"""Group sensor hits into target tracks: one track per probable person.

Pure logic, no I/O. Fusion works out each hit's evidence e and hands it in;
the tracker decides which track the hit joins and keeps each track's state.

Which track a hit joins (windows and limits from config.yaml, tracks section):

1. A device hit (Wi-Fi, BLE) joins the track that already holds its device hash.
2. A hit with a bearing joins the track seen at the nearest bearing within
   bearing_merge_deg, if that bearing is under window_s old.
3. The thermal camera and radar share a bracket and look at one thing at a
   time. Radar gives no bearing, so a radar hit joins the track they last saw,
   if under focus_s ago. So does a thermal hit, if that track has no bearing yet.
4. When they find someone not yet tracked, that person joins the device track
   the searcher was closing in on: a device heard in the last window_s and
   estimated within confirm_range_m, on a track with no recent bearing of its
   own. Known target devices come first, then the strongest signal.
5. Anything else starts a new track.

Each track keeps the strongest evidence each sensor has given it, so confidence
never drops because the searcher walked away or looked elsewhere: a person
confirmed ten minutes ago is still there. Bearing and range are relative to the
searcher, so fusion reports them only while under window_s old. "Recent" means
within that many seconds either way (see within()), so a clock set backwards
mid-search can't make old readings look fresh.

Bounded: device-only tracks are forgotten after keep_s unheard, each track
remembers at most max_devs device hashes, and past max_tracks the least urgent,
longest-quiet track is dropped.
"""

import math
from collections.abc import Collection
from dataclasses import dataclass, field

from common.config import TracksConfig
from common.schema import Hazard, HazardReading, RadarHit, RfHit, ThermalHit


@dataclass(eq=False)
class Track:
    """One probable person. Fusion reads it to build a Target."""

    id: int
    last_t: float  # newest hit of any kind
    rf: dict[str, RfHit] = field(default_factory=dict)  # newest hit per device hash
    bearing_deg: float | None = None
    bearing_t: float = -math.inf  # when bearing_deg was measured
    confirm_t: float = -math.inf  # newest thermal or radar hit
    radar: RadarHit | None = None  # newest radar hit that saw someone
    hazard: Hazard | None = None  # gas reading taken at this track; None: none taken
    # Strongest evidence so far from each sensor; None: nothing from it yet.
    e_rf: float | None = None
    e_thermal: float | None = None
    e_radar: float | None = None
    # Kept by fusion: current priority, and what it last published and when.
    priority: int = 4
    sent_priority: int | None = None
    sent_t: float = -math.inf

    @property
    def alive(self) -> bool:
        """Thermal or radar has seen someone on this track."""
        return self.e_thermal is not None or self.e_radar is not None


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
        self, cfg: TracksConfig, known: Collection[str], next_id: int = 1
    ) -> None:
        self.cfg = cfg
        self.known = frozenset(known)  # hashes of target devices from config
        self.tracks: dict[int, Track] = {}
        self.next_id = next_id
        self.evicted = 0  # tracks dropped because max_tracks was reached
        self._gas: HazardReading | None = None  # newest gas reading

    def add_rf(self, hit: RfHit, e: float) -> Track:
        """A Wi-Fi or BLE hit, with its e_rf."""
        track = self._by_dev(hit.dev)
        if track is None and hit.bearing_deg is not None:
            track = self._by_bearing(hit.bearing_deg, hit.t)
        track = track or self._new(hit.t)
        track.rf.pop(hit.dev, None)  # re-insert, so the dict runs oldest to newest
        track.rf[hit.dev] = hit
        if len(track.rf) > self.cfg.max_devs:
            del track.rf[next(iter(track.rf))]
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
        track = self._focus(hit.t) or self._homing(hit.t) or self._new(hit.t)
        self._confirm(track, hit.t)
        if track.radar is None or hit.t >= track.radar.t:
            track.radar = hit
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
        """Forget device-only tracks unheard for keep_s. Confirmed tracks stay
        until max_tracks pushes them out."""
        stale = [
            track.id
            for track in self.tracks.values()
            if not track.alive and now - track.last_t > self.cfg.keep_s
        ]
        for track_id in stale:
            del self.tracks[track_id]

    def _new(self, t: float) -> Track:
        if len(self.tracks) >= self.cfg.max_tracks:
            drop = max(self.tracks.values(), key=lambda tr: (tr.priority, -tr.last_t))
            del self.tracks[drop.id]
            self.evicted += 1
        track = Track(id=self.next_id, last_t=t)
        self.next_id += 1
        self.tracks[track.id] = track
        return track

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

    def _homing(self, t: float) -> Track | None:
        """The device track the searcher was closing in on (rule 4)."""
        best, best_key = None, None
        for track in self.tracks.values():
            if self._has_bearing(track, t):
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
        gas = self._gas
        if gas is not None and within(t, gas.t, self.cfg.focus_s):
            track.hazard = gas.hazard

    def _see_bearing(self, track: Track, bearing: float, t: float) -> None:
        if t >= track.bearing_t:
            track.bearing_deg, track.bearing_t = bearing, t
