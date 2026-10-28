import asyncio
import contextlib
import datetime as dt
import json
import logging

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from . import signing
from .batcher import DynamicBatcher
from .config import settings
from .metrics import REJECTED_MESSAGES_TOTAL
from .schemas import ClaimMessage

logger = logging.getLogger(__name__)


class FlaggingConsumer:

    def __init__(self, batcher: DynamicBatcher, model_version: str):
        self.batcher = batcher
        self.model_version = model_version
        self.consumer: AIOKafkaConsumer | None = None
        self.producer: AIOKafkaProducer | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self.consumer = AIOKafkaConsumer(
            settings.kafka_input_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            enable_auto_commit=True,
            # A fresh group starts at the beginning of the topic, so claims queued while the model was still loading are scored rather than skipped.
            auto_offset_reset="earliest",
        )
        self.producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await self.consumer.start()
        await self.producer.start()
        self._task = asyncio.create_task(self._run())
        logger.info(
            "Kafka consumer started: %s -> %s (group=%s)",
            settings.kafka_input_topic,
            settings.kafka_output_topic,
            settings.kafka_consumer_group,
        )

    async def stop(self):
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self.consumer:
            await self.consumer.stop()
        if self.producer:
            await self.producer.stop()

    async def _run(self):
        assert self.consumer is not None
        async for msg in self.consumer:
            try:
                await self._handle(msg.value)
            except Exception:
                logger.exception("Failed to process message at offset %s", msg.offset)

    async def _handle(self, payload: dict):
        if not settings.pipeline_allow_unsigned and not signing.verify(payload, settings.pipeline_raw_key):
            REJECTED_MESSAGES_TOTAL.inc()
            logger.warning("Dropping unsigned/forged message on %s (msg_id=%s)",
                           settings.kafka_input_topic, payload.get("msg_id") if isinstance(payload, dict) else None)
            return
        claim = ClaimMessage.model_validate(payload)
        result = await self.batcher.submit(claim.content)
        out = {
            **claim.model_dump(),
            "flagged": result["flagged"],
            "label": result["label"],
            "confidence": result["confidence"],
            "flag_threshold": settings.flag_threshold,
            "model_version": self.model_version,
            "inference_timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        if settings.pipeline_flagged_key:
            out = signing.sign(out, settings.pipeline_flagged_key)
        assert self.producer is not None
        await self.producer.send_and_wait(settings.kafka_output_topic, out)
