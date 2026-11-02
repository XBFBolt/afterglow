import json
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from common.config import get_config
from common.schema import Alert
from map.board import Board
from map.web.server import make_server


@pytest.fixture
def url() -> Iterator[tuple[str, Board]]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = get_config()
    cfg = base.model_copy(
        update={"post": base.post.model_copy(update={"web_port": port})}
    )
    board = Board(cfg)
    server = make_server(board, threading.Lock(), cfg)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", board
    server.shutdown()
    server.server_close()


def test_serves_the_page_with_nothing_from_outside(url: tuple[str, Board]) -> None:
    base, _ = url
    page = urllib.request.urlopen(base + "/", timeout=2).read().decode()
    assert "<title>Afterglow Command Post</title>" in page
    assert "http://" not in page and "https://" not in page  # no internet at the post


def test_serves_the_board_as_json(url: tuple[str, Board]) -> None:
    base, board = url
    board.heard(Alert(id=7, p=1, c=90, alive=True), 0, 0)
    state = json.loads(urllib.request.urlopen(base + "/state", timeout=2).read())
    assert state["victims"][0]["id"] == 7 and state["link"] == "waiting"


def test_anything_else_is_not_found(url: tuple[str, Board]) -> None:
    base, _ = url
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(base + "/../common/config.yaml", timeout=2)
    assert err.value.code == 404
