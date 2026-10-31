"""Device-ID hashing. Call hash_device the moment a MAC or BLE address is captured;
raw identifiers are never logged, published or transmitted."""

import hashlib

from common.config import get_config


def hash_id(raw_id: str, salt: str, chars: int) -> str:
    """Salted SHA-256 of an identifier, truncated to `chars` hex characters.
    Case and separators are ignored, so AA:BB and aa-bb hash the same."""
    norm = "".join(c for c in raw_id.lower() if c.isalnum())
    return hashlib.sha256(f"{salt}:{norm}".encode()).hexdigest()[:chars]


def hash_device(raw_id: str) -> str:
    """hash_id with the salt and length from config."""
    privacy = get_config().privacy
    return hash_id(raw_id, privacy.salt, privacy.hash_chars)
