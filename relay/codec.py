"""What goes over the LoRa link, as bytes. Pure logic, no I/O.

A packet is one type byte, then compact JSON of the message with every
unknown (None) field left out, so nothing unknown is ever sent as 0:

    A{"v":1,"id":701,"p":1,"c":89,"rng":2,"alive":true,"br":14,"hz":1,"t":1793594786}
    B{"v":1,"seq":12,"up":3,"bad":0,"sim":true}

`src` never goes on the air, an alert's `t` is sent as whole seconds, and a
beacon's `t` isn't sent at all: the receiver stamps it with its own clock.
"""

import json
import math

from pydantic import ValidationError

from common.schema import Alert, Beacon, Target

HAZARD_CODE = {"none": 0, "caution": 1, "danger": 2}
_TYPES: dict[bytes, type[Alert] | type[Beacon]] = {b"A": Alert, b"B": Beacon}
_TAGS = {model: tag for tag, model in _TYPES.items()}


def to_alert(target: Target) -> Alert:
    """The compact Alert for a Target. Confidence is rounded down, so the
    operator never sees more certainty than fusion has."""
    rng = target.range_m
    br = target.breaths_per_min
    return Alert(
        t=target.t,
        id=target.id,
        p=target.priority,
        c=math.floor(target.confidence * 100 + 1e-9),  # 0.8 * 100 is 80.00000000000001
        rng=None if rng is None else round(rng),
        alive=target.alive,
        br=None if br is None else round(br),
        hz=None if target.hazard is None else HAZARD_CODE[target.hazard],
    )


def encode(msg: Alert | Beacon, max_bytes: int) -> bytes:
    """The packet for msg; ValueError if it would be over max_bytes."""
    fields = msg.model_dump(exclude={"src"}, exclude_none=True)
    if isinstance(msg, Alert):
        fields["t"] = round(msg.t)
    else:
        del fields["t"]
    body = json.dumps(fields, separators=(",", ":")).encode()
    packet = _TAGS[type(msg)] + body
    if len(packet) > max_bytes:
        name = type(msg).__name__
        raise ValueError(f"{name} is {len(packet)} bytes, over {max_bytes}")
    return packet


def decode(packet: bytes) -> Alert | Beacon:
    """The message in a packet; ValueError for anything that isn't one of ours.
    Errors never quote the packet."""
    model = _TYPES.get(packet[:1])
    if model is None:
        raise ValueError("unknown packet type")
    try:
        fields = json.loads(packet[1:])
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"{model.__name__} packet isn't JSON") from None
    if not isinstance(fields, dict) or "src" in fields:
        raise ValueError(f"{model.__name__} packet has the wrong shape")
    if ("t" in fields) != (model is Alert):
        raise ValueError("an alert carries its time, a beacon none")
    try:
        return model.model_validate(fields)
    except ValidationError as exc:
        # hide_input_in_errors keeps values out of the message.
        count = exc.error_count()
        raise ValueError(f"bad {model.__name__}: {count} error(s)") from None
