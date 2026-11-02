"""The LoRa link, as seen by lora_tx (node end) and lora_rx (command post end).

    radio.send(data, want_ack) -> packet id     node end only
    radio.recv(timeout_s) -> Packet | Ack | None

Packet is data heard over the air (post end); Ack says the packet with that id
arrived (node end). Only the sim radio exists so far; the Meshtastic board
goes behind the same interface in Phase 2.

The sim radio stands in for both boards over UDP on loopback, so nothing
leaves this machine. It behaves like the real link where it matters: the
receiving end acks a packet sent with want_ack (Meshtastic firmware does this
itself), each packet or ack is lost at random with chance relay.sim_loss, and
all of them are lost while the link is cut:

    python -m relay.radio down    # cut the link
    python -m relay.radio up      # restore it
"""

import argparse
import logging
import random
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from common.config import REPO_ROOT, Config, get_config, setup_logging

log = logging.getLogger(__name__)

End = Literal["node", "post"]
_DATA, _ACK = b"D", b"K"
_MAX_FRAME = 512  # over any packet we send; Meshtastic's limit is 233 bytes


@dataclass(frozen=True)
class Packet:
    data: bytes


@dataclass(frozen=True)
class Ack:
    packet_id: int


class Radio(Protocol):
    def send(self, data: bytes, want_ack: bool) -> int: ...
    def recv(self, timeout_s: float) -> Packet | Ack | None: ...
    def close(self) -> None: ...


def link_flag(cfg: Config) -> Path:
    """While this file exists, the sim link is cut."""
    return REPO_ROOT / cfg.tracks.state_dir / "sim_link_down"


class SimRadio:
    def __init__(
        self,
        end: End,
        cfg: Config,
        port: int | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._end = end
        self._loss = cfg.relay.sim_loss
        self._flag = link_flag(cfg)
        self._rng = rng or random.Random()
        self._next_id = self._rng.getrandbits(32)  # Meshtastic ids are random too
        host = cfg.bus.host  # loopback, checked by config
        self._post_addr = (host, port or cfg.relay.sim_port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(self._post_addr if end == "post" else (host, 0))
        self.lost = 0  # frames dropped "in the air"
        self.bad = 0  # frames that weren't ours

    def send(self, data: bytes, want_ack: bool) -> int:
        if self._end != "node":
            raise RuntimeError("only the node end sends")
        packet_id = self._next_id
        self._next_id = (self._next_id + 1) % (1 << 32)
        header = _DATA + bytes([want_ack]) + packet_id.to_bytes(4, "big")
        self._air(header + data, self._post_addr)
        return packet_id

    def recv(self, timeout_s: float) -> Packet | Ack | None:
        """The next frame within timeout_s (0: don't wait), or None."""
        self._sock.settimeout(timeout_s if timeout_s > 0 else 0.0)
        try:
            frame, addr = self._sock.recvfrom(_MAX_FRAME)
        except (TimeoutError, BlockingIOError):
            return None
        except ConnectionError:  # some systems report a send nobody heard here
            return None
        kind = frame[:1]
        if self._end == "post" and kind == _DATA and len(frame) >= 6:
            want_ack, packet_id = frame[1], frame[2:6]
            if want_ack:
                self._air(_ACK + packet_id, addr)
            return Packet(frame[6:])
        if self._end == "node" and kind == _ACK and len(frame) == 5:
            return Ack(int.from_bytes(frame[1:5], "big"))
        self.bad += 1
        return None

    def _air(self, frame: bytes, addr: tuple[str, int]) -> None:
        if self._flag.exists() or self._rng.random() < self._loss:
            self.lost += 1
            return
        try:
            self._sock.sendto(frame, addr)
        except OSError:
            self.lost += 1  # nobody listening is a lost packet, as on air

    def close(self) -> None:
        self._sock.close()


def open_radio(end: End, cfg: Config, sim: bool) -> Radio:
    """The radio for this end; ValueError when the real one is asked for."""
    if not sim:
        raise ValueError("only the sim radio exists so far (Phase 2): run with --sim")
    return SimRadio(end, cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cut or restore the sim LoRa link.")
    parser.add_argument("state", choices=["down", "up"])
    args = parser.parse_args()
    setup_logging()
    flag = link_flag(get_config())
    if args.state == "down":
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    else:
        flag.unlink(missing_ok=True)
    log.info("sim link %s", args.state)


if __name__ == "__main__":
    main()
