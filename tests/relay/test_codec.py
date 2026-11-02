import json

import pytest

from common.config import get_config
from common.schema import Alert, Beacon, Target
from relay.codec import decode, encode, to_alert

MAX = get_config().relay.max_alert_bytes


def _target(**changes: object) -> Target:
    fields = {"t": 1793594786.6, "id": 701, "confidence": 0.894, "priority": 1}
    return Target(**(fields | {"alive": True} | changes))


def test_target_becomes_a_compact_alert() -> None:
    target = _target(range_m=2.4, breaths_per_min=13.6, hazard="caution")
    alert = to_alert(target)
    assert (alert.id, alert.p, alert.c, alert.alive) == (701, 1, 89, True)
    assert (alert.rng, alert.br, alert.hz) == (2, 14, 1)
    assert alert.t == target.t


def test_confidence_is_rounded_down_never_up() -> None:
    assert to_alert(_target(confidence=0.7999)).c == 79  # never shows 80% for a P3
    assert to_alert(_target(confidence=0.8)).c == 80  # 0.8 * 100 is 80.000...01
    assert to_alert(_target(confidence=0.29)).c == 29  # 0.29 * 100 is 28.999...


def test_unknown_stays_unknown_and_off_the_air() -> None:
    alert = to_alert(_target())
    assert alert.rng is None and alert.br is None and alert.hz is None
    fields = json.loads(encode(alert, MAX)[1:])
    assert set(fields) == {"v", "id", "p", "c", "alive", "t"}


def test_alert_round_trip() -> None:
    alert = to_alert(_target(range_m=2, breaths_per_min=14, hazard="danger"))
    back = decode(encode(alert, MAX))
    assert back == alert.model_copy(update={"t": round(alert.t)})


def test_beacon_round_trip_takes_the_receivers_clock() -> None:
    beacon = Beacon(t=5.0, seq=65535, up=0xFFFF, bad=1, sim=True)
    back = decode(encode(beacon, MAX))
    assert isinstance(back, Beacon)
    assert (back.seq, back.up, back.bad, back.sim) == (65535, 0xFFFF, 1, True)
    assert back.t > 5.0  # stamped on arrival


def test_worst_case_alert_fits() -> None:
    # Ids reach six digits after a lost id file (clock-based ids); seven, and
    # a 10 km range, are far past anything a search will see.
    worst = Alert(
        t=4_000_000_000, id=9_999_999, p=4, c=100, rng=9999, alive=False, br=100, hz=2
    )
    assert len(encode(worst, MAX)) <= MAX
    beacon = Beacon(seq=65535, up=0xFFFF, bad=0xFFFF, sim=False)
    assert len(encode(beacon, MAX)) <= 60  # JSON, so not the ~10 bytes packing gives


def test_an_oversize_message_is_refused() -> None:
    with pytest.raises(ValueError, match="over 20"):
        encode(Alert(id=1, p=1, c=90, alive=True), 20)


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        b"X{}",  # unknown type
        b"A\xff\xfe",  # not text
        b"A{not json",
        b"A[1,2]",  # not an object
        b'A{"v":1,"id":1,"p":1,"c":90,"alive":true}',  # no time
        b'A{"v":1,"id":1,"p":1,"c":90,"alive":true,"t":1,"src":"x"}',
        b'A{"v":1,"id":1,"p":9,"c":90,"alive":true,"t":1}',  # no such priority
        b'A{"v":1,"id":1,"p":1,"c":90,"alive":true,"t":1,"brg":40}',  # unknown key
        b'A{"v":2,"id":1,"p":1,"c":90,"alive":true,"t":1}',  # future format
        b'B{"v":1,"seq":1,"up":1,"bad":0,"sim":true,"t":5}',  # beacons carry no time
        b'B{"v":1,"seq":70000,"up":1,"bad":0,"sim":true}',
    ],
)
def test_anything_but_our_own_packets_is_refused(packet: bytes) -> None:
    with pytest.raises(ValueError):
        decode(packet)


def test_errors_never_quote_the_packet() -> None:
    secret = "aa:bb:cc:dd:ee:ff"
    with pytest.raises(ValueError) as err:
        decode(f'A{{"v":1,"id":"{secret}","p":1,"c":9,"alive":true,"t":1}}'.encode())
    assert secret not in str(err.value)
