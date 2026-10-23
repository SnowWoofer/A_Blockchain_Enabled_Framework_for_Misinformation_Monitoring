from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(PIPELINE_RAW_KEY="raw-key", PIPELINE_FLAGGED_KEY="flagged-key")

from app import signing  # noqa: E402
from app.kafka_consumer import FlaggingConsumer  # noqa: E402

RAW = {"msg_id": "tip_1", "source_platform": "public_tip", "content": "A claim.",
       "ingest_timestamp": "2026-01-01T00:00:00Z"}


class FakeBatcher:
    async def submit(self, text):
        return {"flagged": True, "label": "misinformation", "confidence": 0.9}


class FakeProducer:
    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, value):
        self.sent.append((topic, value))


def _consumer():
    c = FlaggingConsumer(FakeBatcher(), "model-v1")
    c.producer = FakeProducer()
    return c


def test_valid_raw_claim_is_scored_and_signed():
    c = _consumer()
    asyncio.run(c._handle(signing.sign(RAW, "raw-key")))
    topic, out = c.producer.sent[0]
    assert topic == "claims.flagged"
    assert out["flagged"] is True and out["msg_id"] == "tip_1"
    assert signing.verify(out, "flagged-key")
    assert not signing.verify(out, "raw-key")


def test_forged_raw_claim_is_dropped():
    c = _consumer()
    asyncio.run(c._handle(dict(RAW)))
    asyncio.run(c._handle({**signing.sign(RAW, "raw-key"), "content": "swapped"}))
    assert c.producer.sent == []
