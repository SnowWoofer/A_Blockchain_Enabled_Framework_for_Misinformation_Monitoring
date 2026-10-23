from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["PIPELINE_FLAGGED_KEY"] = "flagged-key"

from app import signing  # noqa: E402
from app.config import settings  # noqa: E402
from app.consumer import SubmissionConsumer  # noqa: E402

CLAIM = {"msg_id": "tip_1", "flagged": True, "confidence": 0.93, "model_version": "m1",
         "content": "A claim.", "source_platform": "public_tip", "ingest_timestamp": "2026-01-01T00:00:00Z"}


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "processed_db", str(tmp_path / "processed.db"))
    monkeypatch.setattr(settings, "output_path", str(tmp_path / "dead.jsonl"))
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(200, json={"id": f"Qm{len(posted)}"})

    w = SubmissionConsumer()
    w._db = w._open_processed_db()
    w.http = httpx.AsyncClient(base_url="http://gw", transport=httpx.MockTransport(handler))
    w.posted = posted
    return w


def run(coro):
    return asyncio.run(coro)


def test_signed_claim_is_submitted_once(worker):
    message = signing.sign(CLAIM, "flagged-key")
    run(worker._submit(message))
    run(worker._submit(message))  # redelivery / replay
    assert len(worker.posted) == 1
    body = worker.posted[0].read()
    assert b'"label":"1"' in body.replace(b" ", b"")


def test_forged_claims_are_dropped(worker):
    run(worker._submit(dict(CLAIM)))  # unsigned
    run(worker._submit(signing.sign(CLAIM, "raw-key")))  # wrong key (e.g. the ingest worker's)
    tampered = signing.sign(CLAIM, "flagged-key")
    tampered["flagged"] = False
    run(worker._submit(tampered))
    assert worker.posted == []
    assert not Path(settings.output_path).exists()


def test_start_refuses_without_key(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_flagged_key", "")
    with pytest.raises(RuntimeError):
        run(SubmissionConsumer().start())
