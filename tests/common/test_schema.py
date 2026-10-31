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

SAMPLES: dict[str, Message] = {
    "find.wifi": WifiHit(
        dev="2efae4bfcede", rssi_dbm=-71, rssi_smooth=-70.4, est_range_m=6.2, channel=6
    ),
    "find.ble": BleHit(
        dev="2efae4bfcede",
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
        (
            BleHit,
            {
                "dev": "AA:BB:CC:DD:EE:FF",
                "rssi_dbm": -60,
                "rssi_smooth": -60,
                "est_range_m": 1,
            },
        ),
        (
            BleHit,
            {
                "dev": "2efae4bfcede",
                "rssi_dbm": float("inf"),
                "rssi_smooth": -60,
                "est_range_m": 1,
            },
        ),
    ],
)
def test_bad_values_are_rejected(model: type[Message], fields: dict) -> None:
    with pytest.raises(ValidationError):
        model(**fields)


def test_messages_cannot_be_changed_after_validation() -> None:
    msg = SAMPLES["confirm.thermal"]
    with pytest.raises(ValidationError):
        msg.conf = 5.0  # type: ignore[misc]


def test_unknown_values_default_to_none_not_zero() -> None:
    target = Target(id=1, confidence=0.6, priority=3, alive=False)
    assert target.hazard is None and target.breaths_per_min is None
    alert = Alert(id=1, p=3, c=60, alive=False)
    assert alert.br is None and alert.hz is None
