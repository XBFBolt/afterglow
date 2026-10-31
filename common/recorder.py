"""Record every bus message to logs/<start time>-<bus>.jsonl so runs can be replayed.

Each line is {"topic": ..., "msg": {...}}. Lines reach the OS as they arrive and
are forced to disk every log.fsync_s, so a power cut loses at most that much.
Recording pauses, with an error, if free disk drops below log.min_free_mb.

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

from common.bus import BUSES, Subscriber
from common.config import REPO_ROOT, get_config, setup_logging
from common.schema import Message, model_for
from common.service import run_forever

log = logging.getLogger(__name__)


def log_path(log_dir: Path, bus: str, started: datetime) -> Path:
    return log_dir / f"{started:%Y%m%d-%H%M%S}-{bus}.jsonl"


def format_line(topic: str, msg: Message) -> str:
    return json.dumps({"topic": topic, "msg": msg.model_dump(mode="json")}) + "\n"


def parse_line(line: str) -> tuple[str, Message]:
    """Inverse of format_line; ValueError on a bad line."""
    rec = json.loads(line)
    return rec["topic"], model_for(rec["topic"]).model_validate(rec["msg"])


def disk_ok(path: Path, min_free_mb: int) -> bool:
    return shutil.disk_usage(path).free >= min_free_mb * 1024 * 1024


def record(bus: str, path: Path) -> None:
    """Append every message on the bus to path. Runs until an error."""
    cfg = get_config().log
    with Subscriber([""], bus=bus) as sub, path.open("a") as f:
        paused = False
        last_sync = 0.0  # sync and check the disk on the first pass
        while True:
            got = sub.recv(timeout_s=cfg.fsync_s)
            if got is not None and not paused:
                f.write(format_line(*got))
                f.flush()
            if time.monotonic() - last_sync >= cfg.fsync_s:
                os.fsync(f.fileno())
                last_sync = time.monotonic()
                low = not disk_ok(path.parent, cfg.min_free_mb)
                if low != paused:
                    paused = low
                    if paused:
                        log.error("under %d MB free: recording paused", cfg.min_free_mb)
                    else:
                        log.info("disk space back: recording resumed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record bus traffic to .jsonl.")
    parser.add_argument("--bus", choices=BUSES, default="node")
    args = parser.parse_args()
    setup_logging()
    path = log_path(REPO_ROOT / get_config().log.dir, args.bus, datetime.now())
    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("recording %s bus to %s", args.bus, path)
    run_forever("recorder", lambda: record(args.bus, path))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
