"""Play a scripted victim scenario onto the node bus, with no hardware attached.

A searcher walks from 20 m to 2 m of a victim lying still with a phone:

    0 s   victim's phone heard faintly over BLE and Wi-Fi, plus a bystander's device
    ...   the phone gets warmer as the searcher closes in
    35 s  thermal camera sees a person
    50 s  radar sees someone present and not moving
    65 s  radar picks up breathing, about 14 per minute
    70 s  gas sensor reads caution near the victim

    python -m tools.fake_detections               # real time, about 90 s
    python -m tools.fake_detections --speed 10    # ten times faster
"""

import argparse
import logging
import math
import random
import time
from collections.abc import Callable, Iterator

from common.bus import Publisher
from common.config import get_config, setup_logging
from common.ids import hash_device
from common.schema import BleHit, HazardReading, Message, RadarHit, ThermalHit, WifiHit

log = logging.getLogger(__name__)

# The script. Times are seconds from the start of the scenario.
VICTIM_MAC = "02:00:00:00:00:01"  # locally administered, not a real device
BYSTANDER_MAC = "02:00:00:00:00:02"
VICTIM_BEARING_DEG = 40.0
BYSTANDER_RANGE_M = 10.0
START_RANGE_M = 20.0
END_RANGE_M = 2.0
APPROACH_S = 45.0  # searcher reaches END_RANGE_M at this time and stays
THERMAL_FROM_S = 35.0
RADAR_FROM_S = 50.0
BREATHING_FROM_S = 65.0
GAS_CAUTION_FROM_S = 70.0
DURATION_S = 90.0
BLE_PERIOD_S = 0.5
WIFI_BURST_PERIOD_S = 8.0  # phones probe in bursts, one probe per channel
THERMAL_PERIOD_S = 0.5
RADAR_PERIOD_S = 1.0
GAS_PERIOD_S = 1.0
GAS_BASELINE = 180
RSSI_NOISE_DB = 4.0
SMOOTHING = 0.3  # weight of the newest reading in the fake RSSI smoother


def searcher_range_m(t: float) -> float:
    """Distance from searcher to victim at time t."""
    frac = min(t / APPROACH_S, 1.0)
    return START_RANGE_M + (END_RANGE_M - START_RANGE_M) * frac


def _ticks(period_s: float, start_s: float = 0.0) -> Iterator[float]:
    t = start_s
    while t < DURATION_S:
        yield t
        t += period_s


def _rssi(range_m: float, rng: random.Random) -> float:
    rf = get_config()["rf"]
    clean = rf["p1m_dbm"] - 10 * rf["path_loss_n"] * math.log10(range_m)
    return clean + rng.gauss(0, RSSI_NOISE_DB)


def _rf_stream(
    range_at: Callable[[float], float], period_s: float, rng: random.Random
) -> Iterator[tuple[float, float, float, float]]:
    """(t, rssi, smoothed rssi, estimated range) for one device."""
    smooth = None
    for t in _ticks(period_s):
        rssi = _rssi(range_at(t), rng)
        smooth = rssi if smooth is None else smooth + SMOOTHING * (rssi - smooth)
        yield t, rssi, smooth, range_at(t) * rng.uniform(0.8, 1.25)


def _ble(rng: random.Random) -> Iterator[tuple[str, Message]]:
    for mac, range_at in (
        (VICTIM_MAC, searcher_range_m),
        (BYSTANDER_MAC, lambda t: BYSTANDER_RANGE_M),
    ):
        dev = hash_device(mac)
        for t, rssi, smooth, est in _rf_stream(range_at, BLE_PERIOD_S, rng):
            hit = BleHit(
                t=t, dev=dev, rssi_dbm=rssi, rssi_smooth=smooth, est_range_m=est
            )
            yield "find.ble", hit


def _wifi(rng: random.Random) -> Iterator[tuple[str, Message]]:
    dev = hash_device(VICTIM_MAC)
    channels = get_config()["wifi"]["channels"]
    for t, rssi, smooth, est in _rf_stream(searcher_range_m, WIFI_BURST_PERIOD_S, rng):
        for i, channel in enumerate(channels):
            yield (
                "find.wifi",
                WifiHit(
                    t=t + 0.05 * i,
                    dev=dev,
                    rssi_dbm=rssi + rng.gauss(0, 1),
                    rssi_smooth=smooth,
                    est_range_m=est,
                    channel=channel,
                ),
            )


def _thermal(rng: random.Random) -> Iterator[tuple[str, Message]]:
    for t in _ticks(THERMAL_PERIOD_S):
        if t < THERMAL_FROM_S:
            hit = ThermalHit(
                t=t,
                person=False,
                conf=rng.uniform(0.0, 0.2),
                bearing_deg=rng.uniform(0, 359),
                max_temp_c=rng.uniform(22, 25),
            )
        else:
            ramp = min((t - THERMAL_FROM_S) / 15, 1.0)
            hit = ThermalHit(
                t=t,
                person=True,
                conf=min(0.6 + 0.3 * ramp + rng.uniform(-0.03, 0.03), 1.0),
                bearing_deg=VICTIM_BEARING_DEG + rng.uniform(-3, 3),
                max_temp_c=rng.uniform(33.5, 35.0),
            )
        yield "confirm.thermal", hit


def _radar(rng: random.Random) -> Iterator[tuple[str, Message]]:
    for t in _ticks(RADAR_PERIOD_S):
        if t < RADAR_FROM_S:
            hit = RadarHit(
                t=t, present=False, moving=False, range_m=0, conf=rng.uniform(0, 0.1)
            )
        else:
            ramp = min((t - RADAR_FROM_S) / 15, 1.0)
            breathing = t >= BREATHING_FROM_S
            hit = RadarHit(
                t=t,
                present=True,
                moving=False,
                range_m=searcher_range_m(t) + rng.uniform(-0.1, 0.1),
                breaths_per_min=round(14 + rng.uniform(-1, 1), 1)
                if breathing
                else None,
                conf=min(0.6 + 0.3 * ramp + rng.uniform(-0.03, 0.03), 1.0),
            )
        yield "confirm.radar", hit


def _gas(rng: random.Random) -> Iterator[tuple[str, Message]]:
    for t in _ticks(GAS_PERIOD_S):
        caution = t >= GAS_CAUTION_FROM_S
        level = 1.7 if caution else 1.0
        yield (
            "hazard.gas",
            HazardReading(
                t=t,
                hazard="caution" if caution else "none",
                gas_raw=round(GAS_BASELINE * level + rng.gauss(0, 4)),
                temp_c=round(21.5 + rng.gauss(0, 0.2), 1),
            ),
        )


def script(rng: random.Random) -> list[tuple[str, Message]]:
    """Every (topic, message) in the scenario, in time order. Each message's
    `t` is seconds from the start; play() turns it into wall-clock time."""
    events = [
        *_ble(rng),
        *_wifi(rng),
        *_thermal(rng),
        *_radar(rng),
        *_gas(rng),
    ]
    return sorted(events, key=lambda event: event[1].t)


def play(events: list[tuple[str, Message]], speed: float) -> None:
    with Publisher("fake_detections") as pub:
        start = time.time()
        for topic, msg in events:
            at = start + msg.t / speed
            time.sleep(max(0.0, at - time.time()))
            pub.publish(topic, msg.model_copy(update={"t": at}))


def main() -> None:
    parser = argparse.ArgumentParser(description="Play the fake victim scenario.")
    parser.add_argument(
        "--speed", type=float, default=1.0, help="playback speed multiplier"
    )
    parser.add_argument("--seed", type=int, default=7, help="random seed")
    args = parser.parse_args()
    setup_logging()
    events = script(random.Random(args.seed))
    log.info("playing %d messages over %.0f s", len(events), DURATION_S / args.speed)
    play(events, args.speed)
    log.info("scenario done")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
