"""Replay a recorded run through the live pipeline.

    python -m tools.replay logs/<run>.jsonl               # real time
    python -m tools.replay logs/<run>.jsonl --speed 10    # ten times faster

Only sensor topics (find., confirm., hazard.) are replayed by default: the
running fusion, relay and map redo everything after them, so a change to
fusion can be checked against a recorded search. Times are moved to now
(fusion drops messages stamped far from its clock), keeping their spacing.
Lines that don't parse are skipped and counted.
"""

import argparse
import logging
from pathlib import Path

from common.config import setup_logging
from common.recorder import parse_line
from common.schema import Message
from tools.fake_detections import play

log = logging.getLogger(__name__)

SENSOR_TOPICS = ["find.", "confirm.", "hazard."]


def load(path: Path, prefixes: list[str]) -> tuple[list[tuple[str, Message]], int]:
    """(events, bad lines): the recorded messages on the given topic prefixes,
    in time order, each `t` made seconds from the first."""
    events, bad = [], 0
    with path.open() as f:
        for line in f:
            try:
                topic, msg = parse_line(line)
            except ValueError:
                bad += 1
                continue
            if any(topic.startswith(prefix) for prefix in prefixes):
                events.append((topic, msg))
    events.sort(key=lambda event: event[1].t)
    if events:
        t0 = events[0][1].t
        events = [
            (topic, msg.model_copy(update={"t": msg.t - t0})) for topic, msg in events
        ]
    return events, bad


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a recorded run.")
    parser.add_argument("path", type=Path, help="a logs/*.jsonl recording")
    parser.add_argument("--speed", type=float, default=1.0, help="playback speed")
    parser.add_argument(
        "--topics", nargs="+", default=SENSOR_TOPICS, help="topic prefixes to replay"
    )
    args = parser.parse_args()
    if args.speed <= 0:
        parser.error("--speed must be above 0")
    setup_logging()
    events, bad = load(args.path, args.topics)
    if bad:
        log.warning("skipped %d line(s) that didn't parse", bad)
    if not events:
        log.error("nothing to replay on %s", " ".join(args.topics))
        return
    log.info(
        "replaying %d messages over %.0f s", len(events), events[-1][1].t / args.speed
    )
    play(events, args.speed)
    log.info("replay done")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
