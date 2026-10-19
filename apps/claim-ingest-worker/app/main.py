"""Dummy claim ingestion worker — for testing only.

Real claim ingestion (scraping, a feed subscription, whatever the actual
source ends up being) isn't built yet. This is a stand-in: hand it a list of
raw claim texts over HTTP and it queues each one onto claims.raw in exactly
the shape flagging-engine expects, so the rest of the pipeline (flagging ->
submission-worker -> blockchain_gateway -> fact-checking-service) can be
exercised end to end without a real ingestion source."""
import datetime as dt
import json
import logging
import uuid
from contextlib import asynccontextmanager

from aiokafka import AIOKafkaProducer
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel, Field,model_validator

from .config import settings, kafka_tls_options

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        **kafka_tls_options(),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()
    state["producer"] = producer

    yield

    await producer.stop()


app = FastAPI(title="Claim Ingest Worker (dummy/testing)", lifespan=lifespan)


class IngestRequest(BaseModel):
    claims: list[str] = Field(..., min_length=1)
    msg_ids: list[str] | None = None

    @model_validator(mode='after')
    def validate_ids(self):
        if self.msg_ids is not None and (len(self.msg_ids)!=len(self.claims)
            or len(set(self.msg_ids))!=len(self.msg_ids)
            or any(not 1<=len(value)<=256 for value in self.msg_ids)):
            raise ValueError('msg_ids must be unique, 1..256 characters, and match the claims count')
        return self


@app.get("/health")
def health():
    return {"status": "ok" if "producer" in state else "starting"}


@app.post("/ingest")
async def ingest(body: IngestRequest):
    if body.msg_ids is not None and not settings.benchmark_message_ids:
        raise HTTPException(400,'client message IDs are enabled only for benchmark runs')
    producer: AIOKafkaProducer = state["producer"]
    msg_ids = []
    for index,text in enumerate(body.claims):
        msg_id = body.msg_ids[index] if body.msg_ids is not None else f"test_{uuid.uuid4().hex}"
        message = {
            "msg_id": msg_id,
            "source_platform": settings.source_platform,
            "content": text,
            "ingest_timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        await producer.send_and_wait(settings.kafka_output_topic, message)
        msg_ids.append(msg_id)
    logger.info("Queued %d claim(s) onto %s", len(msg_ids), settings.kafka_output_topic)
    return {"queued": len(msg_ids), "msg_ids": msg_ids}
