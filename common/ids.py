"""Device-ID hashing. Call hash_device the moment a MAC or BLE address is captured;
raw identifiers are never logged, published or transmitted."""

import hashlib
import re

from common.config import get_config


def hash_id(raw_id: str, salt: str, chars: int) -> str:
    """Salted SHA-256 of a MAC or BLE address, truncated to `chars` hex characters.
    Case and separators are ignored, so AA:BB:.. and aa-bb-.. hash the same.
    ValueError unless it is 12 hex digits, so a parsing bug can't turn every
    unreadable address into one shared "device". The error never echoes the ID."""
    norm = "".join(c for c in raw_id.lower() if c.isalnum())
    if not re.fullmatch(r"[0-9a-f]{12}", norm):
        raise ValueError("not a 48-bit MAC or BLE address")
    return hashlib.sha256(f"{salt}:{norm}".encode()).hexdigest()[:chars]


def hash_device(raw_id: str) -> str:
    """hash_id with the salt and length from config."""
    privacy = get_config().privacy
    return hash_id(raw_id, privacy.salt, privacy.hash_chars)
