"""Small cryptographic primitives for the commercial customer control plane."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError("编码值无效")
    try:
        return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))
    except Exception:
        raise ValueError("编码值无效") from None


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def new_secret(prefix: str = "") -> str:
    """Create a URL-safe bearer secret. Only its digest should be persisted."""
    return prefix + secrets.token_urlsafe(32)


def secret_digest(value: str, purpose: str) -> str:
    """Domain-separated token digest, suitable for equality lookup in SQLite."""
    if not isinstance(value, str) or not value or not isinstance(purpose, str) or not purpose:
        raise ValueError("令牌或用途无效")
    return hashlib.sha256(("sna-commercial-v1\0" + purpose + "\0" + value).encode("utf-8")).hexdigest()


def public_key_text(key: Ed25519PublicKey) -> str:
    return b64url_encode(key.public_bytes_raw())


def private_key_text(key: Ed25519PrivateKey) -> str:
    return b64url_encode(key.private_bytes_raw())


def sign_payload(private_key: str | Ed25519PrivateKey, payload: Any) -> str:
    key = private_key if isinstance(private_key, Ed25519PrivateKey) else Ed25519PrivateKey.from_private_bytes(b64url_decode(private_key))
    return b64url_encode(key.sign(canonical_json(payload)))


def verify_payload(public_key: str | Ed25519PublicKey, payload: Any, signature: str) -> bool:
    try:
        key = public_key if isinstance(public_key, Ed25519PublicKey) else Ed25519PublicKey.from_public_bytes(b64url_decode(public_key))
        key.verify(b64url_decode(signature), canonical_json(payload))
        return True
    except (ValueError, InvalidSignature):
        return False
