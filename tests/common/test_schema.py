import pytest
from pydantic import ValidationError

from common.schema import (
    TOPICS,
    Alert,
    BleHit,
    HazardReading,
    Message,
    RadarHit,
    Target,
    ThermalHit,
    WifiHit,
    model_for,
    parse,
)

DEV = "2efae4bfcede1097"

SAMPLES: dict[str, Message] = {
    "find.wifi": WifiHit(
        dev=DEV, rssi_dbm=-71, rssi_smooth=-70.4, est_range_m=6.2, channel=6
    ),
    "find.ble": BleHit(
        dev=DEV,
        rssi_dbm=-64,
        rssi_smooth=-65.1,
        est_range_m=4.0,
        bearing_deg=40,
    ),
    "confirm.thermal": ThermalHit(
        person=True, conf=0.9, bearing_deg=41.5, max_temp_c=34.2
    ),
    "confirm.radar": RadarHit(
        present=True, moving=False, range_m=2.1, breaths_per_min=14, conf=0.85
    ),
    "hazard.gas": HazardReading(hazard="caution", gas_raw=310, temp_c=21.4),
    "fuse.target": Target(
        id=7,
        confidence=0.92,
        priority=1,
        alive=True,
        bearing_deg=40,
        range_m=2.1,
        breaths_per_min=14,
        hazard="caution",
        evidence={"rf": 1.0, "thermal": 0.9, "radar": 0.85},
    ),
    "relay.out": Alert(
        id=7, p=1, c=92, x=12, y=-4, brg=40, rng=2, alive=True, br=14, hz=1
    ),
    "relay.in": Alert(id=7, p=1, c=92, alive=True),
}


def _rf(**changes: object) -> dict:
    """Valid RF-hit fields with some changed."""
    fields = {"dev": DEV, "rssi_dbm": -60, "rssi_smooth": -60, "est_range_m": 1}
    return fields | changes


def test_every_topic_has_a_sample() -> None:
    assert SAMPLES.keys() == TOPICS.keys()


@pytest.mark.parametrize("topic", TOPICS)
def test_round_trip_through_json(topic: str) -> None:
    msg = SAMPLES[topic]
    assert type(msg) is model_for(topic)
    assert parse(topic, msg.model_dump_json()) == msg


def test_unknown_topic_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown topic"):
        parse("find.gps", b"{}")


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (
            ThermalHit,
            {"person": True, "conf": 1.2, "bearing_deg": 10, "max_temp_c": 34},
        ),
        (
            ThermalHit,
            {"person": True, "conf": 0.9, "bearing_deg": 360, "max_temp_c": 34},
        ),
        (HazardReading, {"hazard": "smoky", "gas_raw": 1, "temp_c": 20}),
        (Target, {"id": 1, "confidence": 0.5, "priority": 5, "alive": False}),
        (
            Target,
            {
                "id": 1,
                "confidence": 0.5,
                "priority": 3,
                "alive": False,
                "evidence": {"sonar": 1},
            },
        ),
        (Alert, {"id": 1, "p": 2, "c": 101, "alive": True}),
        (Alert, {"id": 1, "p": 2, "c": 90, "alive": True, "hz": 3}),
        (
            RadarHit,
            {"present": True, "moving": False, "range_m": 1, "conf": 0.5, "extra": 1},
        ),
        (
            RadarHit,
            {"present": True, "moving": False, "range_m": float("nan"), "conf": 0.5},
        ),
        (BleHit, _rf(dev="AA:BB:CC:DD:EE:FF")),  # raw MAC
        (BleHit, _rf(dev="aabbccddeeff")),  # raw MAC, separators stripped
        (BleHit, _rf(rssi_dbm=float("inf"))),
        (BleHit, _rf(rssi_dbm=0)),  # a driver's "no reading"
        (BleHit, _rf(rssi_dbm=-127)),  # bleak's "no reading" on Linux
        (BleHit, _rf(rssi_smooth=196)),  # -60 read as an unsigned byte
        (WifiHit, _rf(channel=0)),
        (BleHit, _rf(t=-1)),
        (
            ThermalHit,
            {"person": True, "conf": 0.9, "bearing_deg": 10, "max_temp_c": 900},
        ),
        (RadarHit, {"present": False, "moving": False, "conf": 0.1}),  # empty room
        (RadarHit, {"present": False, "range_m": 0, "conf": 0.1}),
        (RadarHit, {"present": True, "range_m": 2, "conf": 0.8}),  # moving unknown
        (
            RadarHit,
            {
                "present": True,
                "moving": False,
                "range_m": 2,
                "breaths_per_min": 900,
                "conf": 0.8,
            },
        ),
        (HazardReading, {"hazard": "none", "gas_raw": -1}),
        (Target, {"id": 1, "confidence": 0.9, "priority": 1, "alive": True, "x": 5}),
        (
            Target,
            {
                "id": 1,
                "confidence": 0.9,
                "priority": 1,
                "alive": True,
                "breaths_per_min": 900,
            },
        ),
        (Alert, {"id": 1, "p": 1, "c": 90, "alive": True, "y": 3}),  # half a position
        (Alert, {"v": 2, "id": 1, "p": 1, "c": 90, "alive": True}),  # unknown format
    ],
)
def test_bad_values_are_rejected(model: type[Message], fields: dict) -> None:
    with pytest.raises(ValidationError):
        model(**fields)


def test_messages_cannot_be_changed_after_validation() -> None:
    msg = SAMPLES["confirm.thermal"]
    with pytest.raises(ValidationError):
        msg.conf = 5.0  # type: ignore[misc]
    target = SAMPLES["fuse.target"]
    assert isinstance(target, Target)
    with pytest.raises(ValidationError):
        target.evidence.rf = 7.0  # type: ignore[misc]


def test_errors_never_echo_the_rejected_value() -> None:
    # The rejected value may be the raw MAC the schema exists to keep out of logs.
    with pytest.raises(ValidationError) as caught:
        BleHit(dev="aa:bb:cc:dd:ee:ff", rssi_dbm=-60, rssi_smooth=-60, est_range_m=1)
    assert "aa:bb" not in str(caught.value)


def test_timestamps_from_a_broken_clock_are_rejected() -> None:
    with pytest.raises(ValidationError):
        HazardReading(hazard="none", gas_raw=180, t=1e12)  # year ~33,700


def test_unknown_values_default_to_none_not_zero() -> None:
    target = Target(id=1, confidence=0.6, priority=3, alive=False)
    assert target.hazard is None and target.breaths_per_min is None
    alert = Alert(id=1, p=3, c=60, alive=False)
    assert alert.br is None and alert.hz is None
    empty_room = RadarHit(present=False, conf=0.05)
    assert empty_room.moving is None and empty_room.range_m is None
    assert HazardReading(hazard="none", gas_raw=180).temp_c is None
