from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

PBKDF2_ITERATIONS = 600_000
SESSION_SECONDS = 30 * 24 * 60 * 60


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Password length must be between 12 and 128 characters.")
    salt = salt or secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _unb64(salt), int(iterations)
        )
        return hmac.compare_digest(_b64(digest), expected)
    except (ValueError, TypeError):
        return False


def create_session(secret: str, revision: int, *, now: int | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = _b64(
        json.dumps(
            {"sub": "brett", "rev": revision, "iat": issued, "exp": issued + SESSION_SECONDS},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signature = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def read_session(secret: str, token: str, *, now: int | None = None) -> dict[str, Any] | None:
    try:
        payload, signature = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        current = int(now if now is not None else time.time())
        if data.get("sub") != "brett" or int(data.get("exp", 0)) < current:
            return None
        return data
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def csrf_for_session(secret: str, session_token: str) -> str:
    return _b64(
        hmac.new(secret.encode(), f"csrf:{session_token}".encode(), hashlib.sha256).digest()
    )


def verify_csrf(secret: str, session_token: str, supplied: str) -> bool:
    return hmac.compare_digest(csrf_for_session(secret, session_token), supplied)
