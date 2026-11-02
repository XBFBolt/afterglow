import time

import pytest

from common.config import get_config
from common.health import Heart, Watch, states_from_bits
from common.schema import NOTE_MAX, Heartbeat, Message


class FakePub:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Message]] = []

    def publish(self, topic: str, msg: Message) -> None:
        self.sent.append((topic, msg))

    def beats(self) -> list[Heartbeat]:
        return [msg for topic, msg in self.sent if isinstance(msg, Heartbeat)]


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    return now


def test_beats_at_once_then_once_per_period(clock: list[float]) -> None:
    pub = FakePub()
    heart = Heart(pub, sim=True)  # type: ignore[arg-type]
    period = get_config().health.beat_s
    for _ in range(50):  # a busy loop calls beat() far more often than it sends
        heart.beat()
        clock[0] += period / 10
    assert len(pub.beats()) == 5
    assert all(topic == "health.beat" for topic, _ in pub.sent)
    assert all(b.status == "ok" and b.sim for b in pub.beats())


def test_a_fault_between_beats_is_still_reported(clock: list[float]) -> None:
    pub = FakePub()
    heart = Heart(pub, sim=False)  # type: ignore[arg-type]
    period = get_config().health.beat_s
    heart.beat()
    clock[0] += period / 3
    heart.beat("degraded", "serial port gone")
    clock[0] += period / 3
    heart.beat()  # recovered before the beat was due
    clock[0] += period
    heart.beat()
    clock[0] += period
    heart.beat()
    statuses = [(b.status, b.note) for b in pub.beats()]
    assert statuses == [
        ("ok", None),
        ("degraded", "serial port gone"),  # worst since the last beat
        ("ok", None),  # and then it clears
    ]


def test_long_note_is_cut_short(clock: list[float]) -> None:
    pub = FakePub()
    Heart(pub, sim=False).beat("degraded", "x" * 500)  # type: ignore[arg-type]
    assert pub.beats()[0].note == "x" * NOTE_MAX


def test_note_with_a_mac_is_left_off_not_published(
    clock: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    pub = FakePub()
    heart = Heart(pub, sim=False)  # type: ignore[arg-type]
    heart.beat("degraded", "Device with address AA:BB:CC:DD:EE:FF was not found")
    [beat] = pub.beats()
    assert beat.status == "degraded" and beat.note is None
    assert "AA:BB" not in caplog.text


def _beat(src: str, status: str = "ok", sim: bool = False) -> Heartbeat:
    return Heartbeat(src=src, status=status, sim=sim)  # type: ignore[arg-type]


def test_watch_tells_up_degraded_and_down_apart() -> None:
    watch = Watch(["fusion", "recorder", "radar"], down_after_s=6)
    watch.heard(_beat("fusion"), now=0)
    watch.heard(_beat("radar", "degraded"), now=3)
    watch.heard(_beat("stranger"), now=3)  # not listed: ignored
    assert watch.states(5) == {"fusion": "ok", "recorder": "down", "radar": "degraded"}
    assert watch.states(7)["fusion"] == "down"  # quiet over down_after_s
    assert watch.bits(5) == (0b101, 0b100)


def test_watch_sees_sim_only_from_modules_that_are_up() -> None:
    watch = Watch(["fusion", "radar"], down_after_s=6)
    watch.heard(_beat("fusion"), now=0)
    watch.heard(_beat("radar", sim=True), now=0)
    assert watch.sim(5)
    watch.heard(_beat("fusion"), now=8)
    assert not watch.sim(8)  # the sim radar has gone quiet


def test_bits_decode_back_to_states() -> None:
    names = ["fusion", "recorder", "radar"]
    watch = Watch(names, down_after_s=6)
    watch.heard(_beat("fusion", "degraded"), now=0)
    watch.heard(_beat("radar"), now=0)
    assert states_from_bits(names, *watch.bits(1)) == watch.states(1)


def test_shipped_down_after_allows_for_late_beats() -> None:
    health = get_config().health
    assert health.down_after_s > 2 * health.beat_s
