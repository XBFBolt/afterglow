"""Print bus traffic as it happens.

python -m tools.tap                     # everything on the node bus
python -m tools.tap find. confirm.      # just these topics (prefixes work)
python -m tools.tap --bus post          # the command post's bus
"""

import argparse
from datetime import datetime
from typing import Any

from common.bus import BUSES, Subscriber
from common.schema import Message


def _short(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {k: _short(v) for k, v in value.items()}
    return value


def format_message(topic: str, msg: Message) -> str:
    """One line: time, topic, source, then every non-empty field."""
    stamp = datetime.fromtimestamp(msg.t).strftime("%H:%M:%S.%f")[:-3]
    fields = msg.model_dump(exclude={"t", "src"}, exclude_none=True)
    body = " ".join(f"{k}={_short(v)}" for k, v in fields.items())
    return f"{stamp} {topic:<16} {msg.src:<16} {body}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Print bus traffic.")
    parser.add_argument("topics", nargs="*", default=[""], help="topics or prefixes")
    parser.add_argument("--bus", choices=BUSES, default="node")
    args = parser.parse_args()
    with Subscriber(args.topics, bus=args.bus) as sub:
        while True:
            got = sub.recv()
            if got is not None:
                print(format_message(*got), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
