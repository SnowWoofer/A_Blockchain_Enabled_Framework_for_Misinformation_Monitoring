"""Dummy claim ingestion worker — for testing only.

Real claim ingestion (scraping, a feed subscription, whatever the actual
source ends up being) isn't built yet. This is a stand-in: hand it a list of
raw claim texts over HTTP and it queues each one onto claims.raw in exactly
the shape flagging-engine expects, so the rest of the pipeline (flagging ->
submission-worker -> blockchain_gateway -> fact-checking-service) can be
exercised end to end without a real ingestion source."""
import asyncio
import datetime as dt
import json
import logging
import uuid
from contextlib import asynccontextmanager

from aiokafka import AIOKafkaProducer
from fastapi import FastAPI
from pydantic import BaseModel, Field

from .config import settings

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

state: dict = {}


async def _await_kafka(*clients, attempts: int = 40, delay: float = 3.0) -> None:
    """Start Kafka clients, waiting for the broker to accept connections.

    These services and the broker come up together, and the broker takes ~30s
    to pass its health check. aiokafka's start() fails fast, so without this
    the service crashed on boot and relied on Docker's restart policy to try
    again — five restarts and a screenful of tracebacks on every deployment,
    for an entirely expected condition. depends_on cannot express this: each
    service ships its own compose file and kafka is not in it.
    """
    from aiokafka.errors import KafkaConnectionError

    for attempt in range(1, attempts + 1):
        try:
            for client in clients:
                await client.start()
            return
        except KafkaConnectionError:
            if attempt == attempts:
                raise
            if attempt == 1 or attempt % 10 == 0:
                logger.info("Kafka not reachable yet (attempt %d/%d), retrying…", attempt, attempts)
            await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await _await_kafka(producer)
    state["producer"] = producer

    yield

    await producer.stop()


app = FastAPI(title="Claim Ingest Worker (dummy/testing)", lifespan=lifespan)


class IngestRequest(BaseModel):
    claims: list[str] = Field(..., min_length=1)


@app.get("/health")
def health():
    return {"status": "ok" if "producer" in state else "starting"}


@app.post("/ingest")
async def ingest(body: IngestRequest):
    producer: AIOKafkaProducer = state["producer"]
    ingest_ids = []
    for text in body.claims:
        ingest_id = f"{settings.source_platform}_{uuid.uuid4().hex}"
        message = {
            "ingest_id": ingest_id,
            "source_platform": settings.source_platform,
            "content": text,
            "ingest_timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        await producer.send_and_wait(settings.kafka_output_topic, message)
        ingest_ids.append(ingest_id)
    logger.info("Queued %d claim(s) onto %s", len(ingest_ids), settings.kafka_output_topic)
    return {"queued": len(ingest_ids), "ingest_ids": ingest_ids}
