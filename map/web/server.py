"""The command post's web map, at http://<post.web_host>:<post.web_port>/.

Serves one page (index.html, with nothing loaded from outside: the command
post has no internet) and /state, the Board as JSON. The page polls /state
every second and says so loudly when it can't reach this server, so a dead
map never looks like a quiet search.

Standard library only: the HTTP server runs in a thread, and the main loop
feeds the post bus into the Board.

Run with: python -m map.web.server
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from common.bus import Publisher, Subscriber
from common.config import Config, get_config, setup_logging
from common.health import Heart
from common.service import run_forever
from map.board import Board

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("index.html")
INPUTS = ["relay.in", "relay.beacon", "health."]


def make_server(board: Board, lock: threading.Lock, cfg: Config) -> ThreadingHTTPServer:
    """The HTTP server for board; OSError if the port is taken."""
    page = PAGE.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                self._reply(200, "text/html; charset=utf-8", page)
            elif self.path == "/state":
                with lock:
                    state = board.snapshot(time.monotonic())
                self._reply(200, "application/json", json.dumps(state).encode())
            else:
                self._reply(404, "text/plain", b"not found")

        def _reply(self, code: int, kind: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            log.debug(format, *args)  # one line per poll would bury the real log

    return ThreadingHTTPServer((cfg.post.web_host, cfg.post.web_port), Handler)


def run(board: Board, lock: threading.Lock) -> None:
    """Feed the post bus into the board. Sockets open fresh on each (re)start;
    the board survives a crash."""
    beat_s = board.cfg.health.beat_s
    with Subscriber(INPUTS, bus="post") as sub, Publisher("map", bus="post") as pub:
        heart = Heart(pub, sim=board.cfg.sim)
        while True:
            got = sub.recv(timeout_s=beat_s)
            if got is not None:
                with lock:
                    board.heard(got[1], time.monotonic(), time.time())
            heart.beat()


def main() -> None:
    setup_logging()
    cfg = get_config()
    board, lock = Board(cfg), threading.Lock()
    server = make_server(board, lock, cfg)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("web map up: http://%s:%d/", cfg.post.web_host, cfg.post.web_port)
    run_forever("web map", lambda: run(board, lock))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
