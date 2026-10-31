import time

import pytest

from common.config import get_config
from common.service import run_forever


def test_crashes_and_exits_restart_with_doubling_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    outcomes = iter([RuntimeError("serial unplugged"), None, ValueError("bad frame")])

    def loop() -> None:
        outcome = next(outcomes, KeyboardInterrupt())
        if outcome is not None:
            raise outcome

    with pytest.raises(KeyboardInterrupt):  # Ctrl-C still stops it
        run_forever("test", loop)
    first = get_config().service.backoff_min_s
    assert sleeps == [first, first * 2, first * 4]


def test_backoff_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    calls = iter(range(20))

    def loop() -> None:
        if next(calls) == 19:
            raise KeyboardInterrupt
        raise RuntimeError("still broken")

    with pytest.raises(KeyboardInterrupt):
        run_forever("test", loop)
    assert max(sleeps) == get_config().service.backoff_max_s
