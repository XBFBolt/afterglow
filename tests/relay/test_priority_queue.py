from common.config import get_config
from common.schema import Alert
from relay.priority_queue import AlertQueue


def _queue(**changes: object) -> AlertQueue:
    base = {"ack_timeout_s": 10, "retry_max_s": 30, "queue_max": 64}
    return AlertQueue(get_config().relay.model_copy(update=base | changes))


def _alert(track: int, p: int, c: int = 50) -> Alert:
    return Alert(t=1.0, id=track, p=p, c=c, alive=p <= 2)


def _send(q: AlertQueue, now: float, packet_id: int) -> Alert:
    alert = q.next(now)
    assert alert is not None
    q.sent(alert, packet_id, now)
    return alert


def test_most_urgent_first_then_longest_waiting() -> None:
    q = _queue()
    q.put(_alert(1, p=3), 0)
    q.put(_alert(2, p=1), 1)
    q.put(_alert(3, p=3), 2)
    q.put(_alert(4, p=1), 3)
    order = []
    for packet_id in range(4):
        alert = _send(q, 10, packet_id)
        q.acked(packet_id, 10)
        order.append(alert.id)
    assert order == [2, 4, 1, 3]
    assert len(q) == 0


def test_a_newer_alert_replaces_the_queued_one_and_keeps_its_place() -> None:
    q = _queue()
    q.put(_alert(1, p=2), 0)
    q.put(_alert(2, p=2), 1)
    q.put(_alert(1, p=2, c=95), 2)  # track 1 again
    assert len(q) == 2
    assert q.next(3) == _alert(1, p=2, c=95)


def test_only_the_head_goes_and_waits_for_its_ack() -> None:
    q = _queue()
    q.put(_alert(1, p=1), 0)
    q.put(_alert(2, p=3), 0)
    _send(q, 0, packet_id=100)
    assert q.next(5) is None  # the P3 doesn't jump the waiting P1
    assert q.next(10) == _alert(1, p=1)  # no ack: send again


def test_a_new_p1_is_never_held_up_by_a_stuck_low_priority_alert() -> None:
    q = _queue()
    q.put(_alert(1, p=4), 0)
    _send(q, 0, packet_id=100)  # the link is idle, so the P4 goes, and gets stuck
    q.put(_alert(2, p=1), 1)
    assert q.next(1) == _alert(2, p=1)  # at once, not after the P4's timeout


def test_retries_back_off_up_to_the_cap() -> None:
    q = _queue(ack_timeout_s=10, retry_max_s=30)
    q.put(_alert(1, p=1), 0)
    sends, now = [], 0.0
    while now < 200:
        if q.next(now) is not None:
            q.sent(q.next(now), len(sends), now)  # type: ignore[arg-type]
            sends.append(now)
        now += 1
    gaps = [b - a for a, b in zip(sends, sends[1:], strict=False)]
    assert gaps[:3] == [10, 20, 30] and set(gaps[3:]) == {30}


def test_an_ack_delivers_and_removes_the_alert() -> None:
    q = _queue()
    q.put(_alert(1, p=2), 0)
    _send(q, 0, packet_id=7)
    _send(q, 10, packet_id=8)  # retry
    assert q.acked(7, 11) == _alert(1, p=2)  # the first send's ack is enough
    assert len(q) == 0 and q.acked(8, 12) is None


def test_an_ack_for_a_replaced_alert_leaves_the_newer_one_to_send() -> None:
    q = _queue()
    q.put(_alert(1, p=2), 0)
    _send(q, 0, packet_id=7)
    q.put(_alert(1, p=1), 1)  # replaced while in flight
    assert q.acked(7, 2) is None
    assert q.next(2) == _alert(1, p=1)


def test_any_ack_cuts_long_back_offs_short() -> None:
    # The link came back: whatever was backing off retries after one normal
    # timeout, not after its long wait.
    q = _queue(ack_timeout_s=10, retry_max_s=30)
    q.put(_alert(1, p=1), 0)
    for packet_id, now in enumerate([0, 10, 30]):  # waits 10, 20, now 30
        _send(q, now, packet_id)
    assert q.next(45) is None  # backing off until 60
    q.put(_alert(2, p=1), 45)  # a second P1 queues behind the first
    q.acked(99, 45)  # an ack for something long gone: the link is up
    assert q.next(45) == _alert(1, p=1)  # 30 + 10 <= 45


def test_a_full_queue_drops_the_least_urgent() -> None:
    q = _queue(queue_max=2)
    q.put(_alert(1, p=3), 0)
    q.put(_alert(2, p=4), 1)
    q.put(_alert(3, p=1), 2)  # full: the P4 goes
    q.put(_alert(4, p=4), 3)  # full, and no more urgent than anything: dropped
    assert q.dropped == 2 and len(q) == 2
    assert _send(q, 10, packet_id=1).id == 3
    q.acked(1, 10)
    assert _send(q, 10, packet_id=2).id == 1


def test_drop_takes_an_alert_off_unsent() -> None:
    q = _queue()
    q.put(_alert(1, p=1), 0)
    q.put(_alert(2, p=2), 0)
    q.drop(1)
    q.drop(99)  # not queued: nothing happens
    assert q.next(0) == _alert(2, p=2)
