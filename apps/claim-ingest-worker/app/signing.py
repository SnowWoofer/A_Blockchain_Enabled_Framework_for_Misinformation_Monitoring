"""HMAC-SHA256 signatures on pipeline messages.

Kafka in this stack has no client authentication, so anything that can reach
the broker could otherwise drop a claim straight onto claims.flagged and have
it anchored on the ledger under the AI org's identity. Producers sign each
message with the key for its topic; consumers drop anything that does not
verify. claims.raw and claims.flagged use separate keys, so the public-facing
ingest worker can never forge an AI verdict.

(Identical copies live in claim-ingest-worker, flagging-engine and
submission-worker; each service builds from its own directory.)"""
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
