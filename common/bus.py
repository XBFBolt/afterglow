"""Local pub/sub bus. Modules publish with Publisher and listen with Subscriber;
nothing else opens ZeroMQ sockets.

A proxy process sits in the middle (python -m common.bus): publishers connect
to its pub port and subscribers to its sub port, so modules can start, crash
and restart in any order and reconnect on their own. The node and the command
post each run their own bus. Everything binds to localhost only, and every
socket queue is capped at bus.hwm messages.
"""

import argparse
import logging
import time
from typing import Self

import zmq

from common.config import get_config, setup_logging
from common.schema import TOPICS, Message, model_for, parse
from common.service import run_forever

log = logging.getLogger(__name__)

BUSES = ("node", "post")


def bus_ports(bus: str) -> tuple[int, int]:
    """(pub_port, sub_port) of the named bus."""
    cfg = get_config().bus
    ports = {"node": cfg.node, "post": cfg.post}[bus]
    return ports.pub_port, ports.sub_port


def _endpoint(port: int) -> str:
    return f"tcp://{get_config().bus.host}:{port}"


class Publisher:
    """Publishes one module's messages. Stamps `src` on messages that leave it empty."""

    def __init__(self, src: str, bus: str = "node") -> None:
        self.src = src
        self._sock = zmq.Context.instance().socket(zmq.PUB)
        self._sock.setsockopt(zmq.LINGER, 1000)
        self._sock.setsockopt(zmq.SNDHWM, get_config().bus.hwm)
        self._sock.connect(_endpoint(bus_ports(bus)[0]))
        # ZeroMQ drops anything published before the connection is up.
        time.sleep(get_config().bus.settle_s)

    def publish(self, topic: str, msg: Message) -> None:
        """ValueError if msg is the wrong model for topic or isn't valid."""
        if model_for(topic) is not type(msg):
            raise ValueError(f"{type(msg).__name__} doesn't belong on {topic!r}")
        # Validate again on the way out: model_copy(update=...) skips validation,
        # and a bad message must fail here, loudly, not get dropped by every
        # subscriber while this module looks healthy.
        data = msg.model_dump()
        data["src"] = data["src"] or self.src
        msg = type(msg).model_validate(data)
        self._sock.send_multipart([topic.encode(), msg.model_dump_json().encode()])

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Subscriber:
    """Receives messages on the given topics. A topic can be a prefix:
    "find." gets every find topic and "" gets everything."""

    def __init__(self, topics: list[str], bus: str = "node") -> None:
        if not topics:
            raise ValueError('subscribe to at least one topic ("" for all)')
        for prefix in topics:
            if not any(t.startswith(prefix) for t in TOPICS):
                raise ValueError(f"no topic matches {prefix!r}")
        self._sock = zmq.Context.instance().socket(zmq.SUB)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt(zmq.RCVHWM, get_config().bus.hwm)
        self._sock.connect(_endpoint(bus_ports(bus)[1]))
        for prefix in topics:
            self._sock.setsockopt_string(zmq.SUBSCRIBE, prefix)

    def recv(self, timeout_s: float | None = None) -> tuple[str, Message] | None:
        """The next valid (topic, message), or None if none arrives within
        timeout_s. Blocks forever when timeout_s is None. Bad messages are
        logged and skipped."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            wait_ms = None
            if deadline is not None:
                wait_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if not self._sock.poll(wait_ms):
                return None
            frames = self._sock.recv_multipart()
            try:
                topic_b, data = frames
                topic = topic_b.decode()
                return topic, parse(topic, data)
            except ValueError as exc:
                log.warning("dropped bad message: %s", exc)
                # A flood of bad messages must not hold the caller past its timeout.
                if deadline is not None and time.monotonic() >= deadline:
                    return None

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def run_proxy(bus: str = "node", ctx: zmq.Context | None = None) -> None:
    """Forward every message from publishers to subscribers. Blocks until the
    context is terminated."""
    ctx = ctx or zmq.Context.instance()
    pub_port, sub_port = bus_ports(bus)
    hwm = get_config().bus.hwm
    xsub = ctx.socket(zmq.XSUB)
    xpub = ctx.socket(zmq.XPUB)
    xsub.setsockopt(zmq.RCVHWM, hwm)
    xpub.setsockopt(zmq.SNDHWM, hwm)
    try:
        xsub.bind(_endpoint(pub_port))
        xpub.bind(_endpoint(sub_port))
        log.info("%s bus up: publish to :%d, subscribe on :%d", bus, pub_port, sub_port)
        zmq.proxy(xsub, xpub)
    except zmq.ContextTerminated:
        pass
    finally:
        xsub.close(linger=0)
        xpub.close(linger=0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bus proxy.")
    parser.add_argument("--bus", choices=BUSES, default="node")
    args = parser.parse_args()
    setup_logging()
    run_forever(f"{args.bus} bus proxy", lambda: run_proxy(args.bus))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
