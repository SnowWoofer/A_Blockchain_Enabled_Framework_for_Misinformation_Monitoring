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
        consumer = FlaggingConsumer(batcher, model.model_version)
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
    consumer=state.get('consumer')
    if consumer and not consumer.healthy:
        return Response('{"status":"failed","reason":"consumer stopped; inspect durable replay log"}',status_code=503,media_type='application/json')
    return {
        "status": "ok" if model else "starting",
        "device": str(model.device) if model else None,
        "quantization": model.actual_quantization if model else None,
        "requested_quantization": settings.model_quantization,
        "max_batch_size": settings.max_batch_size,
        "max_queue_delay_ms": settings.max_queue_delay_ms,
        "kafka_enabled": settings.kafka_enabled,
        "model_version": model.model_version if model else None,
        "runtime_manifest": model.runtime_manifest if model else None,
    }


@app.post("/predict", response_model=PredictResponse)
async def predict(req: PredictRequest):
    if not req.post_text.strip():
        raise HTTPException(status_code=422, detail="post_text must not be empty")
    batcher: DynamicBatcher = state["batcher"]
    result = await batcher.submit(req.post_text)
    return PredictResponse(**result, model_version=state['model'].model_version)


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
