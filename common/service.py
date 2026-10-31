"""Keep a long-running module alive: log any crash and start it again with back-off."""

import logging
import time
from collections.abc import Callable

from common.config import get_config

log = logging.getLogger(__name__)


def run_forever(name: str, loop: Callable[[], None]) -> None:
    """Call loop() until Ctrl-C. If it raises or returns, log it and call it
    again, waiting backoff_min_s and doubling each time up to backoff_max_s.
    A loop that ran for at least backoff_max_s before failing starts the
    back-off over, so one crash after hours of service restarts quickly."""
    svc = get_config().service
    delay = svc.backoff_min_s
    while True:
        started = time.monotonic()
        try:
            loop()
            log.error("%s stopped unexpectedly", name)
        except Exception:
            log.exception("%s crashed", name)
        if time.monotonic() - started >= svc.backoff_max_s:
            delay = svc.backoff_min_s
        log.info("restarting %s in %.1f s", name, delay)
        time.sleep(delay)
        delay = min(delay * 2, svc.backoff_max_s)
