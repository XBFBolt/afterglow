"""Every message on the bus, as pydantic models.

This file is the contract between modules. All producers and consumers import
from here; never hand-build message dicts. Changing a field is a breaking
change: update every producer, consumer and test in the same commit.

Messages are validated when built, published and received: no NaN, no out-of-range
values, no unknown fields, and device IDs must already be hashed. A value that
isn't known is None, never a default that looks like a real reading.
"""

import re
import time
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Score = Annotated[float, Field(ge=0, le=1)]
Priority = Annotated[int, Field(ge=1, le=4)]  # P1 urgent ... P4 device only
Bearing = Annotated[float, Field(ge=0, lt=360)]  # degrees clockwise
Hazard = Literal["none", "caution", "danger"]
Status = Literal["ok", "degraded"]  # a module's health
# Output of common.ids.hash_device. At least 16 hex characters, so a raw MAC or
# BLE address (12 hex digits) can't pass, with or without separators.
DeviceHash = Annotated[str, Field(pattern=r"^[0-9a-f]{16,64}$")]
# Physical limits: a reading outside them is a bug or a broken sensor, not data.
# "No reading" comes through as a fill value: 0, 127 or -128 dBm from drivers,
# -127 from bleak on Linux. Real signals sit between about -110 and 0.
Rssi = Annotated[float, Field(ge=-120, lt=0)]
WifiChannel = Annotated[int, Field(ge=1, le=14)]  # 2.4 GHz
BreathRate = Annotated[float, Field(ge=0, le=100)]  # breaths per minute
T_MAX = 4_102_444_800.0  # 2100-01-01; a later timestamp is a broken clock
NOTE_MAX = 64  # characters in a Heartbeat note
# A MAC or BLE address in any common spelling: six hex pairs with one separator
# style throughout, or twelve hex digits in a row (also inside a longer hex run,
# so a device hash or a macOS Bluetooth UUID is caught too).
_MAC_LIKE = re.compile(r"[0-9a-f]{2}([:-]?)(?:[0-9a-f]{2}\1){4}[0-9a-f]{2}", re.I)

# Shared by every model. Errors never echo the rejected value: it could be the
# very raw MAC the schema just refused, and errors end up in logs.
STRICT = ConfigDict(
    extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True
)


def _check_position(x: float | None, y: float | None) -> None:
    if (x is None) != (y is None):
        raise ValueError("x and y must be known together")


class Message(BaseModel):
    """Fields every message carries. Messages are immutable once built."""

    model_config = STRICT

    t: float = Field(default_factory=time.time, ge=0, le=T_MAX)  # Unix time, seconds
    src: str = Field(default="", max_length=32)  # module; Publisher fills it in


class RfHit(Message):
    """A device heard over the air."""

    dev: DeviceHash
    rssi_dbm: Rssi
    rssi_smooth: Rssi
    est_range_m: float = Field(ge=0)
    bearing_deg: Bearing | None = None


class WifiHit(RfHit):
    channel: WifiChannel


class BleHit(RfHit):
    pass


class ThermalHit(Message):
    person: bool
    conf: Score
    bearing_deg: Bearing
    max_temp_c: float = Field(ge=-40, le=300)  # MLX90640's measuring range


class RadarHit(Message):
    """When nobody is present, moving, range and breathing are unknown (None),
    never False or 0, so an empty room can't read as a still victim."""

    present: bool
    moving: bool | None = None
    range_m: float | None = Field(default=None, ge=0)
    breaths_per_min: BreathRate | None = None
    conf: Score

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.present and (self.moving is None or self.range_m is None):
            raise ValueError("a present target needs moving and range_m")
        vitals = (self.moving, self.range_m, self.breaths_per_min)
        if not self.present and any(v is not None for v in vitals):
            raise ValueError(
                "nobody present: moving, range_m, breaths_per_min are None"
            )
        return self


class HazardReading(Message):
    hazard: Hazard
    gas_raw: int = Field(ge=0, le=1023)  # Arduino 10-bit ADC
    temp_c: float | None = Field(default=None, ge=-40, le=125)  # None: no sensor


class Evidence(BaseModel):
    """e_i per sensor, 0 to 1. None when that sensor has nothing on the track.
    A model, not a dict, so it is as immutable as the Target holding it."""

    model_config = STRICT

    rf: Score | None = None
    thermal: Score | None = None
    radar: Score | None = None
    audio: Score | None = None


class Target(Message):
    """One fused track: a probable person."""

    id: int = Field(ge=0)
    confidence: Score
    priority: Priority
    alive: bool  # thermal or radar confirms life; False means not confirmed
    bearing_deg: Bearing | None = None
    range_m: float | None = Field(default=None, ge=0)
    breaths_per_min: BreathRate | None = None
    hazard: Hazard | None = None  # None: no recent gas reading
    x: float | None = None  # meters east of the start point, if known
    y: float | None = None  # meters north of the start point, if known
    evidence: Evidence = Field(default_factory=Evidence)

    @model_validator(mode="after")
    def _whole_position(self) -> Self:
        _check_position(self.x, self.y)
        return self


class Alert(Message):
    """What crosses the LoRa link. Short keys and whole numbers keep it small;
    relay/codec.py owns the over-the-air encoding."""

    v: Literal[1] = 1  # format version; any other is rejected, not misread
    id: int = Field(ge=0)  # track id
    p: Priority
    c: int = Field(ge=0, le=100)  # confidence, percent
    x: int | None = None  # meters east of the start point
    y: int | None = None  # meters north of the start point
    brg: Annotated[int, Field(ge=0, lt=360)] | None = None  # bearing, degrees
    rng: Annotated[int, Field(ge=0)] | None = None  # range, meters
    alive: bool
    br: Annotated[int, Field(ge=0, le=100)] | None = None  # breaths per minute
    hz: Annotated[int, Field(ge=0, le=2)] | None = None  # 0 none, 1 caution, 2 danger

    @model_validator(mode="after")
    def _whole_position(self) -> Self:
        _check_position(self.x, self.y)
        return self


class Heartbeat(Message):
    """A module saying it is still running, sent every health.beat_s by
    common.health.Heart. A module that stops beating is down; "degraded" means
    running but blind (sensor unplugged, no frames). Either one must reach the
    operator, so a dead sensor is never read as "no victim here"."""

    status: Status
    sim: bool  # running in sim mode: fake hardware or fake data
    note: str | None = Field(default=None, max_length=NOTE_MAX)  # why, for the operator

    @field_validator("note")
    @classmethod
    def _no_device_address(cls, note: str | None) -> str | None:
        # Free text is the one place a raw MAC could slip out, e.g. a bleak error
        # message passed straight through.
        if note is not None and _MAC_LIKE.search(note):
            raise ValueError("note looks like it holds a device address")
        return note


TOPICS: dict[str, type[Message]] = {
    "find.wifi": WifiHit,
    "find.ble": BleHit,
    "confirm.thermal": ThermalHit,
    "confirm.radar": RadarHit,
    "hazard.gas": HazardReading,
    "fuse.target": Target,
    "relay.out": Alert,
    "relay.in": Alert,
    "health.beat": Heartbeat,
}


def model_for(topic: str) -> type[Message]:
    """The model a topic carries; ValueError for an unknown topic."""
    try:
        return TOPICS[topic]
    except KeyError:
        raise ValueError(f"unknown topic {topic!r}") from None


def parse(topic: str, data: bytes | str) -> Message:
    """Decode a message off the bus; ValueError on an unknown topic or bad data."""
    return model_for(topic).model_validate_json(data)
