"""Load and validate common/config.yaml. Every tunable number in Afterglow comes
from here.

The whole file is checked when a process starts: a missing key, a typo, a value
out of range or thresholds in the wrong order stop the process at launch with a
clear error, never halfway through a search.
"""

import ipaddress
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
    field_validator,
    model_validator,
)

from common.schema import BreathRate, DeviceHash, Rssi, Score, WifiChannel

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(__file__).with_name("config.yaml")

Port = Annotated[int, Field(ge=1024, le=65535)]


class Section(BaseModel):
    # Errors name the key and the rule but never echo the value: a raw MAC typed
    # into targets.devices must not end up in the launch error log.
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class Ports(Section):
    pub_port: Port
    sub_port: Port


class BusConfig(Section):
    host: str
    settle_s: PositiveFloat
    hwm: int = Field(gt=0)
    bad_msg_log_s: PositiveFloat
    node: Ports
    post: Ports

    @field_validator("host")
    @classmethod
    def _loopback(cls, host: str) -> str:
        # Anyone who can reach the bus can inject detections.
        if not ipaddress.IPv4Address(host).is_loopback:
            raise ValueError("the bus must bind to a loopback address, e.g. 127.0.0.1")
        return host

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
    hash_chars: int = Field(ge=16, le=64)  # longer than a raw MAC; see DeviceHash


class TargetsConfig(Section):
    devices: list[DeviceHash]
    known_only: bool

    @model_validator(mode="after")
    def _something_to_find(self) -> Self:
        if self.known_only and not self.devices:
            raise ValueError("known_only needs at least one target device")
        return self


class RfConfig(Section):
    p1m_dbm: Rssi
    # Free space is 2, indoors 2-4; outside 1-6 is a typo, and a tiny n would
    # overflow the range estimate.
    path_loss_n: float = Field(ge=1, le=6)
    smooth_window_s: PositiveFloat
    smooth_max_samples: int = Field(gt=0)
    max_devices: int = Field(gt=0)
    e_full_dbm: Rssi
    e_zero_dbm: Rssi

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
    breath_min: BreathRate
    breath_max: BreathRate
    max_skew_s: PositiveFloat

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.breath_min >= self.breath_max:
            raise ValueError("breath_min must be below breath_max")
        return self


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
    focus_s: PositiveFloat
    radar_merge_m: PositiveFloat
    confirm_range_m: PositiveFloat
    keep_s: PositiveFloat
    max_tracks: int = Field(gt=0)
    max_devs: int = Field(gt=0)
    demote_s: PositiveFloat
    resend_s: PositiveFloat
    state_dir: str

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.focus_s <= self.window_s <= self.keep_s:
            raise ValueError("need focus_s <= window_s <= keep_s")
        return self


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
    channels: list[WifiChannel] = Field(min_length=1)


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


class HealthConfig(Section):
    beat_s: PositiveFloat


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
    health: HealthConfig

    @model_validator(mode="after")
    def _targets_match_hash_length(self) -> Self:
        # A target hashed at another length could never match a hit.
        n = self.privacy.hash_chars
        if any(len(dev) != n for dev in self.targets.devices):
            raise ValueError(f"every target hash must be {n} characters (hash_chars)")
        return self

    @model_validator(mode="after")
    def _markers_stay_fresh(self) -> Self:
        # A P1 marker must be resent before TAK greys it out.
        if self.tracks.resend_s >= self.tak.stale_s:
            raise ValueError("tracks.resend_s must be under tak.stale_s")
        return self

    @model_validator(mode="after")
    def _state_outlives_logs(self) -> Self:
        # Clearing recordings must not reset track ids.
        state = (REPO_ROOT / self.tracks.state_dir).resolve()
        logs = (REPO_ROOT / self.log.dir).resolve()
        if state == logs or logs in state.parents:
            raise ValueError("tracks.state_dir must not be inside log.dir")
        return self


def load_config(path: Path) -> Config:
    """Read and validate a config file; ValueError naming every problem."""
    try:
        return Config.model_validate(yaml.safe_load(path.read_text()))
    except (yaml.YAMLError, ValidationError) as exc:
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
