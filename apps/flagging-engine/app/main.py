import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .batcher import DynamicBatcher
from .config import settings
from .kafka_consumer import FlaggingConsumer
from .model import MisinfoModel
from .schemas import PredictRequest, PredictResponse

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

MODEL_VERSION = "afro-xlmr-large-76L-misinfo-v1"

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Loading model: device=%s quantization=%s batch=%d delay_ms=%d",
        settings.torch_device,
        settings.model_quantization,
        settings.max_batch_size,
        settings.max_queue_delay_ms,
    )
    model = MisinfoModel()
    executor = ThreadPoolExecutor(max_workers=1)
    batcher = DynamicBatcher(
        infer_fn=model.predict_batch,
        max_batch_size=settings.max_batch_size,
        max_queue_delay_ms=settings.max_queue_delay_ms,
        executor=executor,
    )
    await batcher.start()
    state["model"] = model
    state["batcher"] = batcher

    consumer = None
    if settings.kafka_enabled:
        consumer = FlaggingConsumer(batcher, MODEL_VERSION)
        await consumer.start()
    state["consumer"] = consumer

    yield

    if consumer:
        await consumer.stop()
    await batcher.stop()
    executor.shutdown(wait=True)


app = FastAPI(title="Flagging Engine", lifespan=lifespan)


@app.get("/health")
async def health():
    model: MisinfoModel | None = state.get("model")
    return {
        "status": "ok" if model else "starting",
        "device": str(model.device) if model else None,
        "quantization": settings.model_quantization,
        "max_batch_size": settings.max_batch_size,
        "max_queue_delay_ms": settings.max_queue_delay_ms,
        "kafka_enabled": settings.kafka_enabled,
        "model_version": MODEL_VERSION,
    }


@app.post("/predict", response_model=PredictResponse)
async def predict(req: PredictRequest):
    if not req.post_text.strip():
        raise HTTPException(status_code=422, detail="post_text must not be empty")
    batcher: DynamicBatcher = state["batcher"]
    result = await batcher.submit(req.post_text)
    return PredictResponse(**result, model_version=MODEL_VERSION)


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
