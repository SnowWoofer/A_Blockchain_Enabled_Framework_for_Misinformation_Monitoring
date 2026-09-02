import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import settings
from .consumer import SubmissionConsumer

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer = SubmissionConsumer()
    await consumer.start()
    state["consumer"] = consumer

    yield

    await consumer.stop()


app = FastAPI(title="Submission Worker", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok" if state.get("consumer") else "starting",
        "kafka_input_topic": settings.kafka_input_topic,
        "output_path": settings.output_path,
    }


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
