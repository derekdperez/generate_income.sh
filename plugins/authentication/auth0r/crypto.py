
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _build_key_material() -> bytes:
    raw = (
        os.getenv("AUTH0R_ENCRYPTION_KEY", "").strip()
        or os.getenv("COORDINATOR_API_TOKEN", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
        or "auth0r-development-key"
    )
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet() -> Fernet:
    return Fernet(_build_key_material())


def encrypt_text(value: str) -> bytes:
    return get_fernet().encrypt((value or "").encode("utf-8"))


def decrypt_text(value: bytes | memoryview | bytearray | None) -> str:
    if value is None:
        return ""
    raw = bytes(value)
    if not raw:
        return ""
    return get_fernet().decrypt(raw).decode("utf-8")
