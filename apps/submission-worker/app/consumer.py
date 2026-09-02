import asyncio
import contextlib
import json
import logging
import os
import time

import httpx
from aiokafka import AIOKafkaConsumer

from .config import settings
from .metrics import CLAIMS_REGISTERED_TOTAL, CLAIMS_REGISTRATION_FAILED_TOTAL, LAST_WRITE_TIMESTAMP

logger = logging.getLogger(__name__)

# Fields the claim must actually carry. Anything missing is a broken upstream
# contract, not something to paper over with a default — the claim goes to the
# dead-letter file so it can be replayed once the producer is fixed.
REQUIRED_FIELDS = ("msg_id", "confidence", "model_version", "content", "source_platform")


class SubmissionConsumer:
    """Reads scored claims off KAFKA_INPUT_TOPIC (claims.flagged) and submits
    each one to the blockchain gateway's POST /api/reports (writes off-chain
    to IPFS, anchors the hash on-chain). A claim that fails to submit — the
    gateway being down, an invalid API key, etc. — is appended as a line to
    OUTPUT_PATH instead, so nothing is silently dropped and failures can be
    replayed later rather than lost."""

    def __init__(self):
        self.consumer: AIOKafkaConsumer | None = None
        self.http: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
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

    async def _run(self):
        assert self.consumer is not None
        async for msg in self.consumer:
            try:
                await self._submit(msg.value)
            except Exception:
                logger.exception("Failed to register claim at offset %s", msg.offset)

    async def _submit(self, payload: dict):
        assert self.http is not None
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
        CLAIMS_REGISTERED_TOTAL.inc()
        LAST_WRITE_TIMESTAMP.set(time.time())

    def _dead_letter(self, payload: dict, error: str):
        logger.error("Blockchain submission failed, writing to dead-letter file: %s", error)
        with open(settings.output_path, "a") as f:
            f.write(json.dumps({**payload, "_error": error}) + "\n")
