from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from common.config import CONFIG_PATH, Config, get_config, load_config


def test_shipped_config_is_valid() -> None:
    assert isinstance(get_config(), Config)


def _set(path: str, value: Any) -> Callable[[dict], None]:
    def change(data: dict) -> None:
        *parents, key = path.split(".")
        for p in parents:
            data = data[p]
        data[key] = value

    return change


def _drop(section: str) -> Callable[[dict], None]:
    return lambda data: data.pop(section)


@pytest.mark.parametrize(
    "change",
    [
        _set("triage.possible", 0.9),  # possible above confirmed
        _set("bus.post.pub_port", 5550),  # post shares a node port
        _set("hazard.danger_x", 1.2),  # danger below caution
        _set("rf.e_zero_dbm", -50),  # zero point above full point
        _set("fusion.weights.radar", 1.5),  # weight above 1
        _set("fusion.weights.rf", float("nan")),
        _set("targets.devices", ["aa:bb:cc:dd:ee:ff"]),  # raw MAC, not a hash
        _set("bus.node.pub_prot", 5555),  # typo
        _set("log.level", "LOUD"),
        _set("relay.max_alert_bytes", 500),  # over Meshtastic's limit
        _set("bus.host", "0.0.0.0"),  # bus open to the network
        _set("privacy.hash_chars", 12),  # as long as a raw MAC
        _set("privacy.hash_chars", 20),  # targets were hashed at 16
        _set("wifi.channels", [1, 6, 15]),
        _drop("triage"),
        _drop("health"),
        _set("rf.path_loss_n", 0.01),  # would overflow the range estimate
        _set("rf.p1m_dbm", 45),  # sign dropped
        _set("rf.max_devices", 0),
        _set("fusion.breath_min", 40),  # above breath_max
        _set("tracks.focus_s", 60),  # longer than window_s
        _set("tracks.keep_s", 10),  # shorter than window_s
        _set("tracks.max_tracks", 0),
        _set("health.beat_s", 0),
        _set("targets.devices", []),  # known_only with nothing to find
        _set("tracks.resend_s", 120),  # P1 markers would go stale between resends
        _set("tracks.state_dir", "logs/state"),  # clearing logs would reset ids
        _set("tracks.state_dir", "logs"),
        _set("tracks.demote_s", 0),
        _set("tracks.restore_s", 0),
        _set("tracks.radar_drift_mps", -1),
    ],
)
def test_bad_config_fails_at_load(
    change: Callable[[dict], None], tmp_path: Path
) -> None:
    data = yaml.safe_load(CONFIG_PATH.read_text())
    change(data)
    bad = tmp_path / "config.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="invalid config"):
        load_config(bad)


def test_broken_yaml_fails_at_load(tmp_path: Path) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text("sim: true\n  bus: [\n")
    with pytest.raises(ValueError, match="invalid config"):
        load_config(bad)


def test_config_errors_name_the_key_but_never_echo_a_raw_mac(tmp_path: Path) -> None:
    data = yaml.safe_load(CONFIG_PATH.read_text())
    data["targets"]["devices"] = ["aa:bb:cc:dd:ee:ff"]
    bad = tmp_path / "config.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError) as caught:
        load_config(bad)
    assert "targets.devices" in str(caught.value)
    assert "aa:bb" not in str(caught.value)
