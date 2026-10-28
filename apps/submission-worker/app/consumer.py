import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import time

import httpx
from aiokafka import AIOKafkaConsumer

from . import signing
from .config import settings
from .metrics import (
    CLAIMS_DUPLICATE_TOTAL, CLAIMS_REGISTERED_TOTAL, CLAIMS_REGISTRATION_FAILED_TOTAL,
    CLAIMS_REJECTED_TOTAL, LAST_WRITE_TIMESTAMP,
)

logger = logging.getLogger(__name__)

# Fields the claim must actually carry. Anything missing is a broken upstream contract, not something to paper over with a default — the claim goes to the dead-letter file so it can be replayed once the producer is fixed.
REQUIRED_FIELDS = ("msg_id", "confidence", "model_version", "content", "source_platform")


class SubmissionConsumer:
    # Reads scored claims off KAFKA_INPUT_TOPIC (claims.flagged) and submits each one to the blockchain gateway's POST /api/reports (writes off-chain to IPFS, anchors the hash on-chain). 

    def __init__(self):
        self.consumer: AIOKafkaConsumer | None = None
        self.http: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._db: sqlite3.Connection | None = None

    def _open_processed_db(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(settings.processed_db) or ".", exist_ok=True)
        db = sqlite3.connect(settings.processed_db)
        db.execute("CREATE TABLE IF NOT EXISTS processed (msg_id TEXT PRIMARY KEY, report_id TEXT, at REAL)")
        db.commit()
        return db

    def _already_submitted(self, msg_id: str) -> bool:
        assert self._db is not None
        return self._db.execute("SELECT 1 FROM processed WHERE msg_id=?", (msg_id,)).fetchone() is not None

    def _mark_submitted(self, msg_id: str, report_id: str) -> None:
        assert self._db is not None
        self._db.execute("INSERT OR IGNORE INTO processed VALUES (?, ?, ?)", (msg_id, report_id, time.time()))
        self._db.commit()

    async def start(self):
        if not settings.pipeline_flagged_key and not settings.pipeline_allow_unsigned:
            raise RuntimeError("PIPELINE_FLAGGED_KEY is not set (run blockchain/scripts/gen-secrets.sh)")
        self._db = self._open_processed_db()
        os.makedirs(os.path.dirname(settings.output_path) or ".", exist_ok=True)
        self.http = httpx.AsyncClient(
            base_url=settings.blockchain_api_base,
            headers={"X-API-Key": settings.blockchain_api_key},
            timeout=15.0,
        )
        self.consumer = AIOKafkaConsumer(
            settings.kafka_input_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            enable_auto_commit=True,
            # A fresh group starts at the beginning of the topic, so claims scored before this worker joined are submitted rather than skipped.
            auto_offset_reset="earliest",
        )
        await self.consumer.start()
        self._task = asyncio.create_task(self._run())
        logger.info(
            "Submission consumer started: %s -> %s (group=%s, dead-letter=%s)",
            settings.kafka_input_topic,
            settings.blockchain_api_base,
            settings.kafka_consumer_group,
            settings.output_path,
        )

    async def stop(self):
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self.consumer:
            await self.consumer.stop()
        if self.http:
            await self.http.aclose()
        if self._db:
            self._db.close()

    async def _run(self):
        assert self.consumer is not None
        async for msg in self.consumer:
            try:
                await self._submit(msg.value)
            except Exception:
                logger.exception("Failed to register claim at offset %s", msg.offset)

    async def _submit(self, payload: dict):
        assert self.http is not None
        if not settings.pipeline_allow_unsigned and not signing.verify(payload, settings.pipeline_flagged_key):
            # Not dead-lettered: a forged message must not become replayable.
            CLAIMS_REJECTED_TOTAL.inc()
            logger.warning("Dropping unsigned/forged claim (msg_id=%s)", payload.get("msg_id"))
            return
        if payload.get("msg_id") and self._already_submitted(payload["msg_id"]):
            CLAIMS_DUPLICATE_TOTAL.inc()
            logger.info("Skipping already-submitted claim %s", payload["msg_id"])
            return
        missing = [f for f in REQUIRED_FIELDS if not payload.get(f)]
        if missing:
            self._dead_letter(payload, f"claim is missing required field(s): {', '.join(missing)}")
            CLAIMS_REGISTRATION_FAILED_TOTAL.inc()
            return
        body = {
            "msg_id": payload["msg_id"],
            "label": "1" if payload.get("flagged") else "0",
            "confidence": payload["confidence"],
            "model_version": payload["model_version"],
            "content": payload["content"],
            "source_platform": payload["source_platform"],
            "published_at": payload.get("ingest_timestamp", ""),
            "inference_timestamp": payload.get("inference_timestamp", ""),
        }
        try:
            resp = await self.http.post("/api/reports", json=body)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._dead_letter(payload, str(exc))
            CLAIMS_REGISTRATION_FAILED_TOTAL.inc()
            return
        self._mark_submitted(payload["msg_id"], resp.json().get("id", ""))
        CLAIMS_REGISTERED_TOTAL.inc()
        LAST_WRITE_TIMESTAMP.set(time.time())

    def _dead_letter(self, payload: dict, error: str):
        logger.error("Blockchain submission failed, writing to dead-letter file: %s", error)
        with open(settings.output_path, "a") as f:
            f.write(json.dumps({**payload, "_error": error}) + "\n")
