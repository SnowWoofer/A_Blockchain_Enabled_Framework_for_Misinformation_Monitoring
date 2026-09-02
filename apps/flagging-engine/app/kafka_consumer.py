import asyncio
import contextlib
import datetime as dt
import json
import logging

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .batcher import DynamicBatcher
from .config import settings
from .schemas import ClaimMessage

logger = logging.getLogger(__name__)


class FlaggingConsumer:
    """Kafka 'data inject' / 'data egest' side of the Flagging-Engine: reads raw
    claims off KAFKA_INPUT_TOPIC, runs them through the dynamic batcher, and
    writes the verdict (original fields + label/confidence) to
    KAFKA_OUTPUT_TOPIC for the Registration-Service to pick up."""

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
        claim = ClaimMessage.model_validate(payload)
        result = await self.batcher.submit(claim.post_text)
        out = {
            **claim.model_dump(),
            "flagged": result["flagged"],
            "label": result["label"],
            "confidence": result["confidence"],
            "misinformation_probability": result["misinformation_probability"],
            "flag_threshold": settings.flag_threshold,
            "model_version": self.model_version,
            "inference_timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        assert self.producer is not None
        await self.producer.send_and_wait(settings.kafka_output_topic, out)
