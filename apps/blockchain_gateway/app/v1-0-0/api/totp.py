"""RFC 6238 time-based one-time passwords (SHA-1, 6 digits, 30 s steps — the
parameters every mainstream authenticator app defaults to)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP_S = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: Optional[float] = None) -> int:
    return int((time.time() if now is None else now) // STEP_S)


def code_at(secret: str, now: Optional[float] = None) -> str:
    return _code(secret, current_step(now))


def verify(secret: str, code: str, last_step: int = -1, now: Optional[float] = None) -> Optional[int]:
    """The matching time step (±1 step of clock drift), or None. Steps at or
    before `last_step` are refused so a captured code cannot be replayed."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    step = current_step(now)
    for candidate in (step - 1, step, step + 1):
        if candidate > last_step and hmac.compare_digest(_code(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "Misinformation Monitor") -> str:
    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_S}")
