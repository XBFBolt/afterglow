import random
from statistics import mean

import pytest

from common.config import get_config
from common.ids import hash_device
from common.schema import TOPICS, BleHit, HazardReading, Message, RadarHit, ThermalHit
from tools import fake_detections as fake


@pytest.fixture(scope="module")
def events() -> list[tuple[str, Message]]:
    return fake.script(random.Random(7))


def _on(events: list[tuple[str, Message]], topic: str) -> list:
    return [msg for t, msg in events if t == topic]


def test_events_are_valid_and_in_time_order(events: list[tuple[str, Message]]) -> None:
    times = [msg.t for _, msg in events]
    assert times == sorted(times)
    assert 0 <= times[0] and times[-1] <= fake.DURATION_S
    for topic, msg in events:
        assert type(msg) is TOPICS[topic]


def test_victim_phone_gets_warmer(events: list[tuple[str, Message]]) -> None:
    victim = hash_device(fake.VICTIM_MAC)
    hits: list[BleHit] = [h for h in _on(events, "find.ble") if h.dev == victim]
    early = mean(h.rssi_smooth for h in hits[:10])
    late = mean(h.rssi_smooth for h in hits[-10:])
    assert late - early > 15


def test_victim_phone_is_a_configured_target() -> None:
    assert hash_device(fake.VICTIM_MAC) in get_config().targets.devices


def test_heat_then_breathing(events: list[tuple[str, Message]]) -> None:
    thermal: list[ThermalHit] = _on(events, "confirm.thermal")
    assert all(h.person == (h.t >= fake.THERMAL_FROM_S) for h in thermal)
    radar: list[RadarHit] = _on(events, "confirm.radar")
    assert all(h.present == (h.t >= fake.RADAR_FROM_S) for h in radar)
    assert all(
        (h.breaths_per_min is not None) == (h.t >= fake.BREATHING_FROM_S) for h in radar
    )
    assert not any(h.moving for h in radar)


def test_gas_reaches_caution(events: list[tuple[str, Message]]) -> None:
    gas: list[HazardReading] = _on(events, "hazard.gas")
    assert gas[0].hazard == "none"
    assert gas[-1].hazard == "caution"


def test_no_raw_mac_is_ever_published(events: list[tuple[str, Message]]) -> None:
    for mac in (fake.VICTIM_MAC, fake.BYSTANDER_MAC):
        raw = mac.replace(":", "")
        for _, msg in events:
            text = msg.model_dump_json()
            assert mac not in text and raw not in text
