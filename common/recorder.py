"""Record every bus message to logs/<start time>-<bus>.jsonl so runs can be replayed.

Each line is {"topic": ..., "msg": {...}}. Lines reach the OS as they arrive and
are forced to disk every log.fsync_s, so a power cut loses at most that much.
Recording pauses, with an error and a "degraded" heartbeat, if free disk drops
below log.min_free_mb.

Run with: python -m common.recorder
"""

import argparse
import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from common.bus import BUSES, Publisher, Subscriber
from common.config import REPO_ROOT, get_config, setup_logging
from common.health import Heart
from common.schema import Message, model_for
from common.service import run_forever

log = logging.getLogger(__name__)


def log_path(log_dir: Path, bus: str, started: datetime) -> Path:
    return log_dir / f"{started:%Y%m%d-%H%M%S}-{bus}.jsonl"


def format_line(topic: str, msg: Message) -> str:
    return json.dumps({"topic": topic, "msg": msg.model_dump(mode="json")}) + "\n"


def parse_line(line: str) -> tuple[str, Message]:
    """Inverse of format_line; ValueError on a bad line, including valid JSON
    of the wrong shape, so a reader can skip it and carry on."""
    try:
        rec = json.loads(line)
        return rec["topic"], model_for(rec["topic"]).model_validate(rec["msg"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"not a recorded message: {exc!r}") from None


def disk_ok(path: Path, min_free_mb: int) -> bool:
    return shutil.disk_usage(path).free >= min_free_mb * 1024 * 1024


def record(bus: str, path: Path) -> None:
    """Append every message on the bus to path. Runs until an error."""
    cfg = get_config().log
    path.parent.mkdir(parents=True, exist_ok=True)  # in case it was deleted mid-run
    with (
        Subscriber([""], bus=bus) as sub,
        Publisher("recorder", bus=bus) as pub,
        path.open("a") as f,
    ):
        heart = Heart(pub, sim=get_config().sim)
        paused = False
        last_sync = 0.0  # sync and check the disk on the first pass
        while True:
            due = last_sync + cfg.fsync_s
            got = sub.recv(timeout_s=max(0.0, due - time.monotonic()))
            if got is not None and not paused:
                f.write(format_line(*got))
                f.flush()
            if time.monotonic() >= due:
                os.fsync(f.fileno())
                last_sync = time.monotonic()
                low = not disk_ok(path.parent, cfg.min_free_mb)
                if low != paused:
                    paused = low
                    if paused:
                        log.error("under %d MB free: recording paused", cfg.min_free_mb)
                    else:
                        log.info("disk space back: recording resumed")
            if paused:
                heart.beat("degraded", "disk low: recording paused")
            else:
                heart.beat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Record bus traffic to .jsonl.")
    parser.add_argument("--bus", choices=BUSES, default="node")
    args = parser.parse_args()
    setup_logging()
    path = log_path(REPO_ROOT / get_config().log.dir, args.bus, datetime.now())
    log.info("recording %s bus to %s", args.bus, path)
    run_forever("recorder", lambda: record(args.bus, path))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
