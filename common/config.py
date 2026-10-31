"""Load and validate common/config.yaml. Every tunable number in Afterglow comes
from here.

The whole file is checked when a process starts: a missing key, a typo, a value
out of range or thresholds in the wrong order stop the process at launch with a
clear error, never halfway through a search.
"""

import logging
from functools import cache
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    ValidationError,
    model_validator,
)

from common.schema import DeviceHash, Score

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(__file__).with_name("config.yaml")

Port = Annotated[int, Field(ge=1024, le=65535)]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Ports(Section):
    pub_port: Port
    sub_port: Port


class BusConfig(Section):
    host: str
    settle_s: PositiveFloat
    hwm: int = Field(gt=0)
    node: Ports
    post: Ports

    @model_validator(mode="after")
    def _ports_differ(self) -> Self:
        ports = [
            self.node.pub_port,
            self.node.sub_port,
            self.post.pub_port,
            self.post.sub_port,
        ]
        if len(set(ports)) != len(ports):
            raise ValueError("every bus port must be different")
        return self


class ServiceConfig(Section):
    backoff_min_s: PositiveFloat
    backoff_max_s: PositiveFloat

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.backoff_min_s > self.backoff_max_s:
            raise ValueError("backoff_min_s must not exceed backoff_max_s")
        return self


class LogConfig(Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    dir: str
    fsync_s: PositiveFloat
    min_free_mb: int = Field(ge=0)


class PrivacyConfig(Section):
    salt: str = Field(min_length=8)
    hash_chars: int = Field(ge=8, le=64)


class TargetsConfig(Section):
    devices: list[DeviceHash]


class RfConfig(Section):
    p1m_dbm: float
    path_loss_n: PositiveFloat
    smooth_window_s: PositiveFloat
    e_full_dbm: float
    e_zero_dbm: float

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.e_zero_dbm >= self.e_full_dbm:
            raise ValueError("e_zero_dbm must be below e_full_dbm")
        return self


class Weights(Section):
    rf: Score
    thermal: Score
    radar: Score
    audio: Score


class FusionConfig(Section):
    weights: Weights


class TriageConfig(Section):
    confirmed: Score
    possible: Score

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.possible >= self.confirmed:
            raise ValueError("possible must be below confirmed")
        return self


class TracksConfig(Section):
    bearing_merge_deg: float = Field(gt=0, le=180)
    window_s: PositiveFloat
    resend_s: PositiveFloat


class RelayConfig(Section):
    ack_timeout_s: PositiveFloat
    max_alert_bytes: int = Field(gt=0, le=233)  # 233 = Meshtastic's payload limit


class HazardConfig(Section):
    caution_x: float = Field(gt=1)
    danger_x: float

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.danger_x <= self.caution_x:
            raise ValueError("danger_x must be above caution_x")
        return self


class WifiConfig(Section):
    iface: str
    channels: list[Annotated[int, Field(ge=1, le=14)]] = Field(min_length=1)


class ThermalConfig(Section):
    i2c_addr: int = Field(ge=0x03, le=0x77)


class SerialConfig(Section):
    radar: str
    gas: str
    lora: str


class TakConfig(Section):
    url: str
    uid_prefix: str
    stale_s: PositiveFloat


class Config(Section):
    sim: bool
    bus: BusConfig
    service: ServiceConfig
    log: LogConfig
    privacy: PrivacyConfig
    targets: TargetsConfig
    rf: RfConfig
    fusion: FusionConfig
    triage: TriageConfig
    tracks: TracksConfig
    relay: RelayConfig
    hazard: HazardConfig
    wifi: WifiConfig
    thermal: ThermalConfig
    serial: SerialConfig
    tak: TakConfig


def load_config(path: Path) -> Config:
    """Read and validate a config file; ValueError naming every problem."""
    with path.open() as f:
        data = yaml.safe_load(f)
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"invalid config {path}:\n{exc}") from None


@cache
def get_config() -> Config:
    """The shared config, loaded and validated once per process."""
    return load_config(CONFIG_PATH)


def setup_logging() -> None:
    """Configure logging for a module's entry point. Also loads the config, so a
    bad config fails here, at launch."""
    logging.basicConfig(
        level=get_config().log.level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
