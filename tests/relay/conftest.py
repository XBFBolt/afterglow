import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from common.config import Config, get_config
from relay.radio import SimRadio


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Shipped config, with its own state dir (for the link flag), short beats,
    and short ack timeouts so retries happen within a test."""
    base = get_config()
    return base.model_copy(
        update={
            "tracks": base.tracks.model_copy(update={"state_dir": str(tmp_path)}),
            "health": base.health.model_copy(update={"beat_s": 0.05}),
            "relay": base.relay.model_copy(
                update={"ack_timeout_s": 0.05, "retry_max_s": 0.1}
            ),
        }
    )


@pytest.fixture
def radios(cfg: Config) -> Iterator[tuple[SimRadio, SimRadio]]:
    """(node end, post end) on a free port, so tests never touch a running node."""
    port = free_udp_port()
    node, post = SimRadio("node", cfg, port=port), SimRadio("post", cfg, port=port)
    yield node, post
    node.close()
    post.close()
