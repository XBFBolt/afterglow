"""TAK output: each alert received at the command post becomes a CoT marker on
ATAK or WinTAK, sent over UDP to tak.url from the local address tak.send_from.

- uid AG-<track id>, so a repeat alert moves the same marker
- stale after tak.stale_s, so a marker nobody refreshes fades; P1/P2 are
  resent every tracks.resend_s, which config keeps under it
- no GPS: every marker sits at the searcher's position (post.searcher_lat and
  _lon), with its range from the searcher as the circular error and in the
  remarks
- the searcher is a friendly marker, refreshed by each status beacon, so it
  fades too when the link goes down

Run with: python -m map.cot_sender
"""

import ipaddress
import logging
import math
import socket
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from common.bus import Publisher, Subscriber
from common.config import Config, get_config, setup_logging
from common.health import Heart
from common.schema import Alert, Beacon, Message
from common.service import run_forever
from map.board import HAZARD_NAME

log = logging.getLogger(__name__)

INPUTS = ["relay.in", "relay.beacon"]
UNKNOWN = "9999999.0"  # CoT's "not known" for heights and errors
VICTIM_TYPE = "a-n-G"  # neutral ground point
SEARCHER_TYPE = "a-f-G-U-C"  # friendly ground unit
LABEL = {1: "URGENT", 2: "CONFIRMED", 3: "POSSIBLE", 4: "DEVICE ONLY"}


def stamp(t: float) -> str:
    """CoT time, e.g. 2026-11-07T22:00:00.000Z."""
    iso = datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds")
    return iso.replace("+00:00", "Z")


def cot_event(
    uid: str,
    kind: str,
    callsign: str,
    remarks: str,
    ce: float | None,
    now: float,
    cfg: Config,
) -> bytes:
    """One CoT event at the searcher's position. ce: circular error, meters."""
    event = ET.Element(
        "event",
        version="2.0",
        uid=uid,
        type=kind,
        how="h-e",  # position estimated, not measured
        time=stamp(now),
        start=stamp(now),
        stale=stamp(now + cfg.tak.stale_s),
    )
    ET.SubElement(
        event,
        "point",
        lat=f"{cfg.post.searcher_lat:.6f}",
        lon=f"{cfg.post.searcher_lon:.6f}",
        hae=UNKNOWN,
        ce=UNKNOWN if ce is None else f"{ce:.1f}",
        le=UNKNOWN,
    )
    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact", callsign=callsign)
    ET.SubElement(detail, "remarks").text = remarks
    return ET.tostring(event, encoding="utf-8", xml_declaration=True)


def remarks(alert: Alert) -> str:
    """What the operator reads, e.g. "P1 URGENT: alive, breathing 14/min,
    confidence 89%, gas caution, within 2 m of searcher"."""
    parts = [
        "alive" if alert.alive else "life not confirmed",
        "breathing unknown" if alert.br is None else f"breathing {alert.br}/min",
        f"confidence {alert.c}%",
        "gas: no reading" if alert.hz is None else f"gas {HAZARD_NAME[alert.hz]}",
        "near searcher, range unknown"
        if alert.rng is None
        else f"within {alert.rng} m of searcher",
    ]
    return f"P{alert.p} {LABEL[alert.p]}: " + ", ".join(parts)


def victim_event(alert: Alert, now: float, cfg: Config) -> bytes:
    uid = f"{cfg.tak.uid_prefix}{alert.id}"
    callsign = f"P{alert.p} track {alert.id}"
    ce = None if alert.rng is None else float(max(alert.rng, 1))
    return cot_event(uid, VICTIM_TYPE, callsign, remarks(alert), ce, now, cfg)


def searcher_event(now: float, cfg: Config) -> bytes:
    uid = f"{cfg.tak.uid_prefix}searcher"
    return cot_event(uid, SEARCHER_TYPE, "Searcher", "Afterglow node", None, now, cfg)


def open_socket(cfg: Config) -> tuple[socket.socket, tuple[str, int]]:
    """A UDP socket sending from tak.send_from, and where to send."""
    host, port = cfg.tak.url.removeprefix("udp://").rsplit(":", 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if ipaddress.IPv4Address(host).is_multicast:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)  # this LAN only
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_MULTICAST_IF,
            socket.inet_aton(cfg.tak.send_from),
        )
    else:
        sock.bind((cfg.tak.send_from, 0))
    return sock, (host, int(port))


def to_event(msg: Message, now: float, cfg: Config) -> bytes | None:
    """The CoT event a post bus message calls for, if any."""
    match msg:
        case Alert():
            return victim_event(msg, now, cfg)
        case Beacon():
            return searcher_event(now, cfg)
    return None


def run(cfg: Config) -> None:
    """Sockets open fresh on each (re)start."""
    sock, dest = open_socket(cfg)
    failed, last_report = 0, -math.inf  # sends failed since the last report
    failing = False  # the latest send failed
    try:
        with (
            Subscriber(INPUTS, bus="post") as sub,
            Publisher("cot_sender", bus="post") as pub,
        ):
            heart = Heart(pub, sim=cfg.sim)
            while True:
                got = sub.recv(timeout_s=cfg.health.beat_s)
                event = None if got is None else to_event(got[1], time.time(), cfg)
                if event is not None:
                    try:
                        sock.sendto(event, dest)
                        failing = False
                    except OSError:  # network down; markers resume when it's back
                        failed, failing = failed + 1, True
                mono = time.monotonic()
                if failed and mono - last_report >= cfg.bus.bad_msg_log_s:
                    log.warning("%d CoT event(s) couldn't be sent", failed)
                    failed, last_report = 0, mono
                if failing:
                    heart.beat("degraded", "CoT sends failing")
                else:
                    heart.beat()
    finally:
        sock.close()


def main() -> None:
    setup_logging()
    cfg = get_config()
    log.info("cot_sender up: %s from %s", cfg.tak.url, cfg.tak.send_from)
    run_forever("cot_sender", lambda: run(cfg))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
