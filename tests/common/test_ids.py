from common.config import get_config
from common.ids import hash_device, hash_id
from common.schema import BleHit


def test_hash_is_truncated_hex() -> None:
    h = hash_id("AA:BB:CC:DD:EE:FF", "salt", 12)
    assert len(h) == 12
    int(h, 16)


def test_case_and_separators_do_not_matter() -> None:
    a = hash_id("AA:BB:CC:DD:EE:FF", "salt", 12)
    assert a == hash_id("aa-bb-cc-dd-ee-ff", "salt", 12)
    assert a == hash_id("aabbccddeeff", "salt", 12)


def test_salt_changes_the_hash() -> None:
    assert hash_id("aa:bb", "one", 12) != hash_id("aa:bb", "two", 12)


def test_hash_device_uses_config() -> None:
    privacy = get_config().privacy
    mac = "02:00:00:00:00:01"
    assert hash_device(mac) == hash_id(mac, privacy.salt, privacy.hash_chars)


def test_hash_is_accepted_by_the_schema() -> None:
    BleHit(
        dev=hash_device("AA:BB:CC:DD:EE:FF"),
        rssi_dbm=-60,
        rssi_smooth=-60,
        est_range_m=1,
    )
