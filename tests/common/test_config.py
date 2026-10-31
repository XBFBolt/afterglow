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
        _drop("triage"),
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
