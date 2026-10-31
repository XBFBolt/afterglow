import pytest

from common.config import get_config
from common.ids import hash_device, hash_id
from common.schema import BleHit

MAC = "AA:BB:CC:DD:EE:FF"


def test_hash_is_truncated_hex() -> None:
    h = hash_id(MAC, "salt", 16)
    assert len(h) == 16
    int(h, 16)


def test_case_and_separators_do_not_matter() -> None:
    a = hash_id(MAC, "salt", 16)
    assert a == hash_id("aa-bb-cc-dd-ee-ff", "salt", 16)
    assert a == hash_id("aabb.ccdd.eeff", "salt", 16)
    assert a == hash_id("aabbccddeeff", "salt", 16)


def test_salt_changes_the_hash() -> None:
    assert hash_id(MAC, "one", 16) != hash_id(MAC, "two", 16)


@pytest.mark.parametrize(
    "raw", ["", "None", "aa:bb:cc", "aa:bb:cc:dd:ee:ff:00", "zz:bb:cc:dd:ee:ff"]
)
def test_garbage_ids_are_rejected_without_echoing_them(raw: str) -> None:
    # Hashing these would merge every unreadable address into one "device".
    with pytest.raises(ValueError) as exc:
        hash_id(raw, "salt", 16)
    assert raw == "" or raw not in str(exc.value)


def test_hash_device_uses_config() -> None:
    privacy = get_config().privacy
    mac = "02:00:00:00:00:01"
    assert hash_device(mac) == hash_id(mac, privacy.salt, privacy.hash_chars)


def test_hash_is_accepted_by_the_schema() -> None:
    BleHit(dev=hash_device(MAC), rssi_dbm=-60, rssi_smooth=-60, est_range_m=1)
