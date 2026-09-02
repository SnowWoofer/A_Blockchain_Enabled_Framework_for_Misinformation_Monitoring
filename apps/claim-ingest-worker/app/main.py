"""Dummy claim ingestion worker — for testing only.

Real claim ingestion (scraping, a feed subscription, whatever the actual
source ends up being) isn't built yet. This is a stand-in: hand it a list of
raw claim texts over HTTP and it queues each one onto claims.raw in exactly
the shape flagging-engine expects, so the rest of the pipeline (flagging ->
submission-worker -> blockchain_gateway -> fact-checking-service) can be
exercised end to end without a real ingestion source."""
import datetime as dt
import hashlib
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()
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
    msg_ids = []
    for text in body.claims:
        msg_id = f"test_{uuid.uuid4().hex}"
        message = {
            "msg_id": msg_id,
            "source_platform": settings.source_platform,
            "post_text": text,
            "ingest_timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            # No real user exists for a dummy test claim — hash the message
            # ID itself as a structurally-valid placeholder.
            "masked_user_hash": hashlib.sha256(msg_id.encode("utf-8")).hexdigest(),
        }
        await producer.send_and_wait(settings.kafka_output_topic, message)
        msg_ids.append(msg_id)
    logger.info("Queued %d claim(s) onto %s", len(msg_ids), settings.kafka_output_topic)
    return {"queued": len(msg_ids), "msg_ids": msg_ids}
