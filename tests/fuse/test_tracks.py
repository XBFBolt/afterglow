import pytest

from common.config import TracksConfig, get_config
from common.schema import BleHit, Hazard, HazardReading, RadarHit, ThermalHit
from fuse.tracks import Tracker, angle_between

PHONE = "1" * 16
OTHER = "2" * 16
KNOWN = "3" * 16  # a target device from config


def _cfg(**changes: object) -> TracksConfig:
    base = {
        "bearing_merge_deg": 20,
        "window_s": 30,
        "focus_s": 5,
        "confirm_range_m": 8,
        "keep_s": 600,
        "max_tracks": 256,
        "max_devs": 16,
    }
    return get_config().tracks.model_copy(update=base | changes)


def _tracker(**changes: object) -> Tracker:
    return Tracker(_cfg(**changes), known=[KNOWN])


def ble(
    dev: str,
    t: float,
    rssi: float = -70,
    rng: float = 5.0,
    bearing: float | None = None,
) -> BleHit:
    return BleHit(
        t=t,
        dev=dev,
        rssi_dbm=rssi,
        rssi_smooth=rssi,
        est_range_m=rng,
        bearing_deg=bearing,
    )


def thermal(t: float, bearing: float, conf: float = 0.9) -> ThermalHit:
    return ThermalHit(t=t, person=True, conf=conf, bearing_deg=bearing, max_temp_c=34)


def radar(t: float, moving: bool = False, breaths: float | None = None) -> RadarHit:
    return RadarHit(
        t=t,
        present=True,
        moving=moving,
        range_m=2.0,
        breaths_per_min=breaths,
        conf=0.8,
    )


def gas(t: float, hazard: Hazard) -> HazardReading:
    return HazardReading(t=t, hazard=hazard, gas_raw=300)


@pytest.mark.parametrize(
    ("a", "b", "angle"), [(40, 55, 15), (355, 5, 10), (5, 355, 10), (0, 180, 180)]
)
def test_angle_between_wraps_through_north(a: float, b: float, angle: float) -> None:
    assert angle_between(a, b) == angle


def test_same_device_joins_its_track_however_long_ago() -> None:
    tr = _tracker()
    first = tr.add_rf(ble(PHONE, 0), 0.3)
    assert tr.add_rf(ble(PHONE, 500), 0.3) is first
    assert tr.add_rf(ble(OTHER, 501), 0.3) is not first


def test_bearing_within_merge_angle_and_window_joins() -> None:
    tr = _tracker()
    first = tr.add_thermal(thermal(0, 40), 0.9)
    assert tr.add_thermal(thermal(10, 55), 0.9) is first  # 15 degrees
    assert tr.add_thermal(thermal(11, 80), 0.9) is not first  # 25 from 55
    assert tr.add_rf(ble(PHONE, 12, bearing=50), 0.5) is first


def test_bearing_merge_wraps_through_north() -> None:
    tr = _tracker()
    assert tr.add_thermal(thermal(0, 355), 0.9) is tr.add_thermal(thermal(1, 5), 0.9)


def test_an_old_bearing_does_not_join() -> None:
    tr = _tracker()
    first = tr.add_thermal(thermal(0, 40), 0.9)
    assert tr.add_thermal(thermal(31, 40), 0.9) is not first


def test_the_nearest_bearing_wins() -> None:
    tr = _tracker()
    at_40 = tr.add_thermal(thermal(0, 40), 0.9)
    at_70 = tr.add_thermal(thermal(1, 70), 0.9)
    assert at_40 is not at_70
    assert tr.add_thermal(thermal(2, 52), 0.9) is at_40  # at_40 now reads 52
    assert tr.add_thermal(thermal(3, 66), 0.9) is at_70


def test_a_person_joins_the_known_device_being_closed_in_on() -> None:
    tr = _tracker()
    tr.add_rf(ble(OTHER, 0, rssi=-55, rng=3), 1.0)  # stronger, but unknown
    known = tr.add_rf(ble(KNOWN, 0, rssi=-65, rng=6), 0.8)
    assert tr.add_thermal(thermal(1, 40), 0.9) is known


def test_with_no_known_device_the_strongest_nearby_wins() -> None:
    tr = _tracker()
    tr.add_rf(ble(OTHER, 0, rssi=-65, rng=6), 0.8)
    strong = tr.add_rf(ble(PHONE, 0, rssi=-55, rng=3), 1.0)
    assert tr.add_radar(radar(1), 0.8) is strong


def test_a_device_too_far_or_too_long_ago_is_not_adopted() -> None:
    tr = _tracker()
    far = tr.add_rf(ble(PHONE, 0, rng=20), 0.2)
    assert tr.add_thermal(thermal(1, 40), 0.9) is not far
    tr = _tracker()
    old = tr.add_rf(ble(PHONE, 0, rng=3), 1.0)
    assert tr.add_thermal(thermal(31, 40), 0.9) is not old


def test_a_second_person_at_another_bearing_gets_their_own_track() -> None:
    tr = _tracker()
    phone = tr.add_rf(ble(PHONE, 0, rng=3), 1.0)
    assert tr.add_thermal(thermal(1, 40), 0.9) is phone
    assert tr.add_thermal(thermal(2, 120), 0.9) is not phone


def test_radar_joins_what_the_camera_just_saw() -> None:
    tr = _tracker()
    seen = tr.add_thermal(thermal(0, 40), 0.9)
    assert tr.add_radar(radar(3), 0.8) is seen
    assert tr.add_radar(radar(7), 0.8) is seen  # radar keeps its own focus alive
    assert tr.add_radar(radar(20), 0.8) is not seen  # 13 s with nothing


def test_thermal_joins_a_radar_only_track() -> None:
    tr = _tracker()
    radar_only = tr.add_radar(radar(0), 0.8)
    assert tr.add_thermal(thermal(1, 40), 0.9) is radar_only
    assert radar_only.bearing_deg == 40


def test_radar_keeps_its_newest_presence_hit() -> None:
    tr = _tracker()
    track = tr.add_radar(radar(2, moving=True), 0.8)
    tr.add_radar(radar(1, moving=False), 0.8)  # arrived late
    assert track.radar is not None and track.radar.moving is True


def test_gas_describes_only_the_track_the_searcher_is_at() -> None:
    tr = _tracker()
    device_only = tr.add_rf(ble(PHONE, 0, rng=20), 0.2)
    seen = tr.add_thermal(thermal(0, 40), 0.9)
    assert tr.add_gas(gas(1, "danger")) is seen
    assert seen.hazard == "danger" and device_only.hazard is None
    # Searcher has moved on: a later reading describes somewhere else.
    assert tr.add_gas(gas(20, "none")) is None
    assert seen.hazard == "danger"


def test_gas_just_before_a_confirmation_counts() -> None:
    tr = _tracker()
    tr.add_gas(gas(0, "caution"))
    assert tr.add_thermal(thermal(2, 40), 0.9).hazard == "caution"
    tr = _tracker()
    tr.add_gas(gas(0, "caution"))
    assert tr.add_thermal(thermal(10, 40), 0.9).hazard is None  # unknown, not "none"


def test_each_sensor_keeps_its_strongest_evidence() -> None:
    tr = _tracker()
    track = tr.add_rf(ble(PHONE, 0), 0.3)
    tr.add_rf(ble(PHONE, 1), 0.9)
    tr.add_rf(ble(PHONE, 2), 0.2)  # searcher walking away
    assert track.e_rf == 0.9
    assert track.e_thermal is None and track.e_radar is None
    assert not track.alive
    tr.add_radar(radar(3), 0.6)
    assert track.alive and track.e_radar == 0.6


def test_track_count_is_bounded_and_the_least_urgent_goes_first() -> None:
    tr = _tracker(max_tracks=3)
    urgent = tr.add_rf(ble(PHONE, 0), 1.0)
    urgent.priority = 1
    quiet = tr.add_rf(ble(OTHER, 1), 0.1)
    recent = tr.add_rf(ble(KNOWN, 2), 0.1)
    newest = tr.add_rf(ble("4" * 16, 3), 0.1)
    assert set(tr.tracks.values()) == {urgent, recent, newest}
    assert tr.evicted == 1
    assert newest.id == 4  # ids are never reused
    assert quiet.id not in tr.tracks


def test_devices_per_track_are_bounded() -> None:
    tr = _tracker(max_devs=2)
    track = tr.add_rf(ble(PHONE, 0, bearing=40), 0.5)
    tr.add_rf(ble(OTHER, 1, bearing=41), 0.5)
    tr.add_rf(ble(KNOWN, 2, bearing=42), 0.5)
    assert list(track.rf) == [OTHER, KNOWN]


def test_prune_forgets_device_only_tracks_but_keeps_confirmed_ones() -> None:
    tr = _tracker()
    device_only = tr.add_rf(ble(PHONE, 0, rng=20), 0.2)
    confirmed = tr.add_thermal(thermal(0, 40), 0.9)
    tr.prune(now=500)
    assert set(tr.tracks.values()) == {device_only, confirmed}
    tr.prune(now=601)
    assert set(tr.tracks.values()) == {confirmed}


def test_ids_start_where_told() -> None:
    tr = Tracker(_cfg(), known=[], next_id=500)
    assert tr.add_rf(ble(PHONE, 0), 0.1).id == 500
    assert tr.next_id == 501
