"""Load common/config.yaml. Every tunable number in Afterglow comes from here."""

import logging
from functools import cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(__file__).with_name("config.yaml")


def load_config(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


@cache
def get_config() -> dict[str, Any]:
    """The shared config, loaded once per process."""
    return load_config(CONFIG_PATH)


def setup_logging() -> None:
    """Configure logging for a module's entry point."""
    logging.basicConfig(
        level=get_config()["log"]["level"],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
