import asyncio
import base64
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
            enable_auto_commit=False,
            auto_offset_reset='earliest',
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

    @property
    def healthy(self):
        return self._task is not None and not self._task.done()

    async def _run(self):
        assert self.consumer is not None
        async for msg in self.consumer:
            self._kafka_meta={'topic':msg.topic,'partition':msg.partition,'offset':msg.offset}
            try:
                payload = json.loads(msg.value.decode('utf-8'))
            except (ValueError,UnicodeError):
                self._dead_letter({'raw_base64':base64.b64encode(msg.value).decode('ascii')},'invalid JSON')
            else:
                await self._submit(payload)
            # Do not continue after a failed durable write or offset commit.
            # Replay is safe because the gateway deduplicates (org,msg_id).
            from aiokafka import TopicPartition
            await self.consumer.commit({TopicPartition(msg.topic,msg.partition):msg.offset+1})

    async def _submit(self, payload: dict):
        assert self.http is not None
        if not isinstance(payload,dict):
            self._dead_letter({'invalid_payload':payload},'claim must be an object')
            CLAIMS_REGISTRATION_FAILED_TOTAL.inc()
            return
        missing = [f for f in (*REQUIRED_FIELDS,'flagged') if payload.get(f) is None or payload.get(f)=='']
        if missing:
            self._dead_letter(payload, f"claim is missing required field(s): {', '.join(missing)}")
            CLAIMS_REGISTRATION_FAILED_TOTAL.inc()
            return
        if not isinstance(payload['flagged'],bool):
            self._dead_letter(payload,'flagged must be a boolean')
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
        for attempt in range(3):
            try:
                resp = await self.http.post("/api/reports", json=body)
                resp.raise_for_status()
                break
            except httpx.HTTPError as exc:
                retryable = not isinstance(exc,httpx.HTTPStatusError) or exc.response.status_code>=500 or (
                    exc.response.status_code==409 and 'commit rejected' in exc.response.text)
                if attempt==2 or not retryable:
                    self._dead_letter(payload, type(exc).__name__)
                    CLAIMS_REGISTRATION_FAILED_TOTAL.inc()
                    return
                await asyncio.sleep(.5*2**attempt)
        CLAIMS_REGISTERED_TOTAL.inc()
        LAST_WRITE_TIMESTAMP.set(time.time())

    def _dead_letter(self, payload: dict, error: str):
        logger.error("Blockchain submission failed, writing to dead-letter file: %s", error)
        with open(settings.output_path, "a", encoding='utf-8') as f:
            f.write(json.dumps({**payload, "_error": error,'_kafka':getattr(self,'_kafka_meta',None)}) + "\n")
            f.flush()
            os.fsync(f.fileno())
