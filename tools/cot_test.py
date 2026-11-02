"""Send a test marker to TAK, to check that a TAK app can see us, without the
rest of the pipeline.

    uv run python -m tools.cot_test --lat 40.71280 --lon -74.00600

Sends one marker, "AFTERGLOW TEST", at that spot every 2 s for 2 minutes
(Ctrl-C stops it), to tak.url from this computer's address on the local
network (found automatically; --from picks one). --to sends straight to one
device instead, e.g. --to 192.168.1.42:4242. The data is fake, but CoT isn't
encrypted: use your own Wi-Fi, never a public one.

Unlike a real victim, the test marker goes stale after STALE_S, and a delete
is sent when the test ends, so it doesn't linger on the map.
"""

import argparse
import socket
import time
import uuid
import xml.etree.ElementTree as ET

from pydantic import ValidationError

from common.config import Config, PostConfig, TakConfig, get_config
from map.cot_sender import UNKNOWN, VICTIM_TYPE, cot_event, open_socket, stamp

PERIOD_S = 2.0
STALE_S = 10.0
UID = "AG-test"


def lan_address() -> str:
    """This computer's address on the network its default route uses.
    OSError if there is none (no network). Sends nothing: connecting a UDP
    socket only picks a route."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1: documentation only, never used
        return s.getsockname()[0]


def marker_config(cfg: Config, lat: float, lon: float, to: str, frm: str) -> Config:
    """cfg with the test's destination, source and position; ValueError if
    any of them is invalid."""
    try:
        tak = TakConfig.model_validate(
            cfg.tak.model_dump()
            | {"url": f"udp://{to}", "send_from": frm, "stale_s": STALE_S}
        )
        post = PostConfig.model_validate(
            cfg.post.model_dump() | {"searcher_lat": lat, "searcher_lon": lon}
        )
    except ValidationError as exc:
        raise ValueError(str(exc)) from None
    return cfg.model_copy(update={"tak": tak, "post": post})


def delete_event(uid: str, now: float) -> bytes:
    """TAK's "delete this item" message for uid."""
    event = ET.Element(
        "event",
        version="2.0",
        uid=str(uuid.uuid4()),
        type="t-x-d-d",
        how="h-g-i-g-o",
        time=stamp(now),
        start=stamp(now),
        stale=stamp(now + STALE_S),
    )
    ET.SubElement(event, "point", lat="0", lon="0", hae=UNKNOWN, ce=UNKNOWN, le=UNKNOWN)
    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "link", uid=uid, relation="none", type="none")
    ET.SubElement(detail, "__forcedelete")
    return ET.tostring(event, encoding="utf-8", xml_declaration=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a test marker to TAK.")
    parser.add_argument("--lat", type=float, required=True, help="latitude")
    parser.add_argument("--lon", type=float, required=True, help="longitude")
    default_to = get_config().tak.url.removeprefix("udp://")
    parser.add_argument("--to", default=default_to, help="address:port to send to")
    parser.add_argument("--from", dest="frm", help="local address to send from")
    parser.add_argument("--seconds", type=float, default=120, help="how long")
    args = parser.parse_args()
    try:
        frm = args.frm or lan_address()
        cfg = marker_config(get_config(), args.lat, args.lon, args.to, frm)
    except OSError:
        parser.error("no network found: join a Wi-Fi network, or pass --from")
    except ValueError as exc:
        parser.error(f"bad --lat, --lon, --to or --from:\n{exc}")
    sock, dest = open_socket(cfg)
    print(f"sending a test marker to {args.to} from {frm} every {PERIOD_S:.0f} s")
    end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < end:
            event = cot_event(
                UID,
                VICTIM_TYPE,
                "AFTERGLOW TEST",
                "If you can see this, TAK can see Afterglow",
                None,
                time.time(),
                cfg,
            )
            try:
                sock.sendto(event, dest)
                print(".", end="", flush=True)
            except OSError as exc:
                print(f"\nsend failed: {exc}", flush=True)
            time.sleep(PERIOD_S)
    finally:
        try:
            sock.sendto(delete_event(UID, time.time()), dest)
        except OSError:
            pass  # it goes stale after STALE_S anyway
        sock.close()
        print("\ndone; the marker is deleted, or fades within 10 s")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
