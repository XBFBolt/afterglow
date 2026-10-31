"""Record every bus message to logs/<start time>-<bus>.jsonl so runs can be replayed.

Each line is {"topic": ..., "msg": {...}}. Run with: python -m common.recorder
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from common.bus import BUSES, Subscriber
from common.config import REPO_ROOT, get_config, setup_logging
from common.schema import Message, model_for

log = logging.getLogger(__name__)


def log_path(log_dir: Path, bus: str, started: datetime) -> Path:
    return log_dir / f"{started:%Y%m%d-%H%M%S}-{bus}.jsonl"


def format_line(topic: str, msg: Message) -> str:
    return json.dumps({"topic": topic, "msg": msg.model_dump(mode="json")}) + "\n"


def parse_line(line: str) -> tuple[str, Message]:
    """Inverse of format_line; ValueError on a bad line."""
    rec = json.loads(line)
    return rec["topic"], model_for(rec["topic"]).model_validate(rec["msg"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Record bus traffic to .jsonl.")
    parser.add_argument("--bus", choices=BUSES, default="node")
    args = parser.parse_args()
    setup_logging()
    path = log_path(REPO_ROOT / get_config()["log"]["dir"], args.bus, datetime.now())
    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("recording %s bus to %s", args.bus, path)
    with Subscriber([""], bus=args.bus) as sub, path.open("a") as f:
        while True:
            got = sub.recv()
            if got is None:
                continue
            try:
                f.write(format_line(*got))
                f.flush()
            except OSError as exc:
                log.error("couldn't write %s: %s", path, exc)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
