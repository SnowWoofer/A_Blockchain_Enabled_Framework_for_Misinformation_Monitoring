import hashlib
import hmac
import json

SIG_FIELD = "_sig"


def _canonical(message: dict) -> bytes:
    body = {k: v for k, v in message.items() if k != SIG_FIELD}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(message: dict, key: str) -> dict:
    digest = hmac.new(key.encode("utf-8"), _canonical(message), hashlib.sha256).hexdigest()
    return {**message, SIG_FIELD: digest}


def verify(message: dict, key: str) -> bool:
    signature = message.get(SIG_FIELD)
    if not key or not isinstance(signature, str):
        return False
    expected = hmac.new(key.encode("utf-8"), _canonical(message), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)
