"""Field end of the LoRa link: send targets to the command post as alerts.

Subscribes to fuse.target and health.beat on the node bus. Each Target becomes
a compact Alert (relay/codec.py) in the priority queue (relay/priority_queue.py),
which decides what goes next and when to retry. Every relay.beacon_s a status
beacon goes too, without an ack: which modules are up, and proof the link is
alive. It never goes ahead of an alert that is ready, so a P1 is never kept
waiting by it. Every alert sent, retries included, is published on relay.out.

Run with: python -m relay.lora_tx [--sim]
"""

import argparse
import logging
import math
import sys
import time
from dataclasses import dataclass

from common.bus import Publisher, Subscriber
from common.config import Config, get_config, setup_logging
from common.health import Heart, Watch
from common.schema import Beacon, Heartbeat, Target
from common.service import run_forever
from relay.codec import encode, to_alert
from relay.priority_queue import AlertQueue
from relay.radio import Ack, Radio, open_radio

log = logging.getLogger(__name__)

INPUTS = ["fuse.target", "health.beat"]
TICK_S = 0.1  # longest wait for the bus each pass; acks and retries wait no longer
MAX_ACKS_PER_PASS = 100


@dataclass
class TxState:
    """What lora_tx keeps across loop restarts, so a crash doesn't lose the queue."""

    cfg: Config
    sim: bool
    queue: AlertQueue
    watch: Watch
    seq: int = 0
    beacon_due: float = -math.inf  # monotonic; send the first one at once
    unsendable: int = 0  # alerts encode() refused
    reported: tuple[int, int] = (0, 0)  # (queue drops, unsendable) last reported
    last_report: float = -math.inf  # monotonic


def new_state(cfg: Config, sim: bool) -> TxState:
    watch = Watch(cfg.health.node_modules, cfg.health.down_after_s)
    return TxState(cfg, sim, AlertQueue(cfg.relay), watch)


def step(sub: Subscriber, pub: Publisher, radio: Radio, state: TxState) -> None:
    """One pass: take at most one bus message, collect acks, send what's due.
    Returns within about TICK_S."""
    cfg = state.cfg
    got = sub.recv(timeout_s=TICK_S)
    now = time.monotonic()
    if got is not None:
        _, msg = got
        if isinstance(msg, Target):
            state.queue.put(to_alert(msg), now)
        elif isinstance(msg, Heartbeat):
            state.watch.heard(msg, now)
    for _ in range(MAX_ACKS_PER_PASS):
        event = radio.recv(timeout_s=0)
        if event is None:
            break
        if isinstance(event, Ack):
            alert = state.queue.acked(event.packet_id, now)
            if alert is not None:
                log.info("track %d (P%d) delivered", alert.id, alert.p)
    alert = state.queue.next(now)
    if alert is not None:
        try:
            packet = encode(alert, cfg.relay.max_alert_bytes)
        except ValueError:
            state.queue.drop(alert.id)  # it never will fit; don't block the line
            state.unsendable += 1
        else:
            state.queue.sent(alert, radio.send(packet, want_ack=True), now)
            pub.publish("relay.out", alert)
    if now >= state.beacon_due:
        _send_beacon(radio, state, now)
    _report(state, now)


def _send_beacon(radio: Radio, state: TxState, now: float) -> None:
    cfg = state.cfg
    up, bad = state.watch.bits(now)
    sim = state.sim or state.watch.sim(now)
    beacon = Beacon(seq=state.seq, up=up, bad=bad, sim=sim)
    radio.send(encode(beacon, cfg.relay.max_alert_bytes), want_ack=False)
    state.seq = (state.seq + 1) % (1 << 16)
    state.beacon_due = now + cfg.relay.beacon_s


def _report(state: TxState, now: float) -> None:
    """Warn about dropped alerts at most once per bus.bad_msg_log_s, with counts."""
    dropped, unsendable = state.queue.dropped, state.unsendable
    if (dropped, unsendable) == state.reported:
        return
    if now - state.last_report < state.cfg.bus.bad_msg_log_s:
        return
    old_dropped, old_unsendable = state.reported
    if dropped > old_dropped:
        log.warning(
            "queue full: dropped %d least urgent alert(s)", dropped - old_dropped
        )
    if unsendable > old_unsendable:
        log.error(
            "dropped %d alert(s) over relay.max_alert_bytes",
            unsendable - old_unsendable,
        )
    state.reported = (dropped, unsendable)
    state.last_report = now


def run(state: TxState) -> None:
    """The relay loop. Sockets and radio open fresh on each (re)start; the
    queue in state survives a crash."""
    radio = open_radio("node", state.cfg, state.sim)
    try:
        with Subscriber(INPUTS) as sub, Publisher("lora_tx") as pub:
            heart = Heart(pub, sim=state.sim)
            while True:
                step(sub, pub, radio, state)
                heart.beat()
    finally:
        radio.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send alerts over LoRa.")
    parser.add_argument("--sim", action="store_true", help="use the sim radio")
    args = parser.parse_args()
    setup_logging()
    cfg = get_config()
    sim = args.sim or cfg.sim
    if not sim:
        log.error("only the sim radio exists so far (Phase 2): run with --sim")
        sys.exit(1)
    state = new_state(cfg, sim)
    log.info("lora_tx up (sim radio)")
    run_forever("lora_tx", lambda: run(state))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
