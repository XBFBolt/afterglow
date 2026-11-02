"""Command post end of the LoRa link: decode what arrives and put it on the
post bus, alerts on relay.in and status beacons on relay.beacon.

The radio acks each alert itself. A packet that isn't one of ours (another
Meshtastic user, a garbled frame) is counted and dropped; the count is
logged at most once per bus.bad_msg_log_s. A retried alert can arrive twice:
the map keys markers by track id, so a repeat just refreshes one.

Run with: python -m relay.lora_rx [--sim]
"""

import argparse
import logging
import math
import sys
import time
from dataclasses import dataclass

from common.bus import Publisher
from common.config import Config, get_config, setup_logging
from common.health import Heart
from common.schema import Alert
from common.service import run_forever
from relay.codec import decode
from relay.radio import Packet, Radio, open_radio

log = logging.getLogger(__name__)


@dataclass
class RxState:
    cfg: Config
    sim: bool
    bad: int = 0  # packets dropped since the last report
    last_report: float = -math.inf  # monotonic


def step(radio: Radio, pub: Publisher, state: RxState) -> None:
    """One pass: wait up to health.beat_s for a packet and publish what it holds."""
    event = radio.recv(timeout_s=state.cfg.health.beat_s)
    if isinstance(event, Packet):
        try:
            msg = decode(event.data)
        except ValueError:
            state.bad += 1
        else:
            pub.publish("relay.in" if isinstance(msg, Alert) else "relay.beacon", msg)
    now = time.monotonic()
    if state.bad and now - state.last_report >= state.cfg.bus.bad_msg_log_s:
        log.warning("dropped %d packet(s) that weren't ours", state.bad)
        state.bad = 0
        state.last_report = now


def run(state: RxState) -> None:
    radio = open_radio("post", state.cfg, state.sim)
    try:
        with Publisher("lora_rx", bus="post") as pub:
            heart = Heart(pub, sim=state.sim)
            while True:
                step(radio, pub, state)
                heart.beat()
    finally:
        radio.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Receive alerts over LoRa.")
    parser.add_argument("--sim", action="store_true", help="use the sim radio")
    args = parser.parse_args()
    setup_logging()
    cfg = get_config()
    sim = args.sim or cfg.sim
    if not sim:
        log.error("only the sim radio exists so far (Phase 2): run with --sim")
        sys.exit(1)
    state = RxState(cfg, sim)
    log.info("lora_rx up (sim radio)")
    run_forever("lora_rx", lambda: run(state))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
