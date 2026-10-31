"""Every message on the bus, as pydantic models.

This file is the contract between modules. All producers and consumers import
from here; never hand-build message dicts. Changing a field is a breaking
change: update every producer, consumer and test in the same commit.
"""

import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Score = Annotated[float, Field(ge=0, le=1)]
Priority = Annotated[int, Field(ge=1, le=4)]  # P1 urgent ... P4 device only
Bearing = Annotated[float, Field(ge=0, lt=360)]  # degrees clockwise
Hazard = Literal["none", "caution", "danger"]
Sensor = Literal["rf", "thermal", "radar", "audio"]


class Message(BaseModel):
    """Fields every message carries."""

    model_config = ConfigDict(extra="forbid")

    t: float = Field(default_factory=time.time)  # Unix time, seconds
    src: str = ""  # module name; Publisher fills it in when empty


class RfHit(Message):
    """A device heard over the air. `dev` is already hashed (common/ids.py)."""

    dev: str
    rssi_dbm: float
    rssi_smooth: float
    est_range_m: float = Field(ge=0)
    bearing_deg: Bearing | None = None


class WifiHit(RfHit):
    channel: int


class BleHit(RfHit):
    pass


class ThermalHit(Message):
    person: bool
    conf: Score
    bearing_deg: Bearing
    max_temp_c: float


class RadarHit(Message):
    present: bool
    moving: bool
    range_m: float = Field(ge=0)
    breaths_per_min: float | None = None
    conf: Score


class HazardReading(Message):
    hazard: Hazard
    gas_raw: int
    temp_c: float


class Target(Message):
    """One fused track: a probable person."""

    id: int = Field(ge=0)
    confidence: Score
    priority: Priority
    alive: bool
    bearing_deg: Bearing | None = None
    range_m: float | None = Field(default=None, ge=0)
    breaths_per_min: float | None = None
    hazard: Hazard = "none"
    x: float | None = None  # meters east of the start point, if known
    y: float | None = None  # meters north of the start point, if known
    evidence: dict[Sensor, Score] = Field(default_factory=dict)  # e_i per sensor


class Alert(Message):
    """What crosses the LoRa link. Short keys and whole numbers keep it small;
    relay/codec.py owns the over-the-air encoding."""

    v: int = 1  # format version
    id: int = Field(ge=0)  # track id
    p: Priority
    c: int = Field(ge=0, le=100)  # confidence, percent
    x: int | None = None  # meters east of the start point
    y: int | None = None  # meters north of the start point
    brg: Annotated[int, Field(ge=0, lt=360)] | None = None  # bearing, degrees
    rng: Annotated[int, Field(ge=0)] | None = None  # range, meters
    alive: bool
    br: int = Field(default=0, ge=0)  # breaths per minute, 0 = unknown
    hz: int = Field(default=0, ge=0, le=2)  # hazard: 0 none, 1 caution, 2 danger


TOPICS: dict[str, type[Message]] = {
    "find.wifi": WifiHit,
    "find.ble": BleHit,
    "confirm.thermal": ThermalHit,
    "confirm.radar": RadarHit,
    "hazard.gas": HazardReading,
    "fuse.target": Target,
    "relay.out": Alert,
    "relay.in": Alert,
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
