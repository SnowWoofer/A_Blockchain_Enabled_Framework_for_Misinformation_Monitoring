from __future__ import annotations
import os
import sys
from pathlib import Path

os.environ.update(PIPELINE_RAW_KEY="raw-key", INGEST_API_KEY="ingest-key", TIP_RATE_LIMIT="3",
                  TRUST_PROXY_HEADERS="true")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main, signing  # noqa: E402


class FakeProducer:
    sent: list = []

    def __init__(self, **kwargs):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send_and_wait(self, topic, value):
        FakeProducer.sent.append((topic, value))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "AIOKafkaProducer", FakeProducer)
    main.tip_limiter._hits.clear()
    FakeProducer.sent.clear()
    with TestClient(main.app) as c:
        yield c


def test_bulk_ingest_requires_key(client):
    assert client.post("/ingest", json={"claims": ["x"]}).status_code == 401
    assert client.post("/ingest", json={"claims": ["x"]}, headers={"X-API-Key": "nope"}).status_code == 401
    resp = client.post("/ingest", json={"claims": ["A claim."]}, headers={"X-API-Key": "ingest-key"})
    assert resp.status_code == 200 and resp.json()["queued"] == 1


def test_messages_are_signed(client):
    client.post("/ingest", json={"claims": ["A claim."]}, headers={"X-API-Key": "ingest-key"})
    topic, message = FakeProducer.sent[0]
    assert topic == "claims.raw"
    assert signing.verify(message, "raw-key")
    assert not signing.verify({**message, "content": "tampered"}, "raw-key")
    assert not signing.verify(message, "flagged-key")


def test_public_tip(client):
    resp = client.post("/public/tip", json={"claim": "  Water is being shut off in Soweto tomorrow.\x07 "},
                       headers={"X-Real-IP": "203.0.113.5"})
    assert resp.status_code == 200
    ref = resp.json()["reference"]
    assert ref.startswith("tip_")
    _, message = FakeProducer.sent[-1]
    assert message["msg_id"] == ref and message["source_platform"] == "public_tip"
    assert message["content"] == "Water is being shut off in Soweto tomorrow."


def test_public_tip_validation_and_rate_limit(client):
    ip = {"X-Real-IP": "198.51.100.7"}
    assert client.post("/public/tip", json={"claim": "short"}, headers=ip).status_code == 422
    assert client.post("/public/tip", json={"claim": "x" * 2001}, headers=ip).status_code == 422
    ok = {"claim": "A sufficiently long claim text."}
    assert client.post("/public/tip", json=ok, headers=ip).status_code == 200
    assert client.post("/public/tip", json=ok, headers=ip).status_code == 429
    assert client.post("/public/tip", json=ok, headers={"X-Real-IP": "198.51.100.8"}).status_code == 200
