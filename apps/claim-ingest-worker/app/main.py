import collections
import datetime as dt
import hmac
import json
import logging
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Deque, Dict, Optional

from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import signing
from .config import settings

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

state: dict = {}

# Control characters other than tab/newline have no business in a claim.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.pipeline_raw_key and not settings.pipeline_allow_unsigned:
        raise RuntimeError("PIPELINE_RAW_KEY is not set (run blockchain/scripts/gen-secrets.sh)")
    if not settings.ingest_api_key:
        logger.warning("INGEST_API_KEY is not set: POST /ingest is disabled")
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()
    state["producer"] = producer

    yield

    await producer.stop()


app = FastAPI(title="Claim Ingest Worker", lifespan=lifespan)


class IngestRequest(BaseModel):
    claims: list[str] = Field(..., min_length=1)


class TipRequest(BaseModel):
    claim: str = Field(..., max_length=20_000)


class _RateLimiter:
    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self._hits: Dict[str, Deque[float]] = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


tip_limiter = _RateLimiter(settings.tip_rate_limit, settings.tip_rate_window_s)


def _clean(text: str) -> str:
    return _CONTROL.sub("", text).strip()


def _message(msg_id: str, content: str, platform: str) -> dict:
    message = {
        "msg_id": msg_id,
        "source_platform": platform,
        "content": content,
        "ingest_timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return signing.sign(message, settings.pipeline_raw_key) if settings.pipeline_raw_key else message


def _client_ip(request: Request) -> str:
    if settings.trust_proxy_headers and request.headers.get("x-real-ip"):
        return request.headers["x-real-ip"].strip()
    return request.client.host if request.client else "unknown"


@app.get("/health")
def health():
    return {"status": "ok" if "producer" in state else "starting"}


@app.post("/ingest")
async def ingest(body: IngestRequest, x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    if not settings.ingest_api_key:
        raise HTTPException(status_code=503, detail="bulk ingest disabled (INGEST_API_KEY not configured)")
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.ingest_api_key):
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")
    if len(body.claims) > settings.max_claims_per_request:
        raise HTTPException(status_code=413, detail=f"at most {settings.max_claims_per_request} claims per request")
    texts = [_clean(t) for t in body.claims]
    if any(not t or len(t) > settings.max_claim_chars for t in texts):
        raise HTTPException(status_code=422, detail=f"each claim must be 1-{settings.max_claim_chars} characters")
    producer: AIOKafkaProducer = state["producer"]
    msg_ids = []
    for text in texts:
        msg_id = f"test_{uuid.uuid4().hex}"
        await producer.send_and_wait(settings.kafka_output_topic, _message(msg_id, text, settings.source_platform))
        msg_ids.append(msg_id)
    logger.info("Queued %d claim(s) onto %s", len(msg_ids), settings.kafka_output_topic)
    return {"queued": len(msg_ids), "msg_ids": msg_ids}


@app.post("/public/tip")
async def public_tip(body: TipRequest, request: Request):
    if not tip_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="too many submissions from your network; please try again later")
    text = _clean(body.claim)
    if not settings.tip_min_chars <= len(text) <= settings.tip_max_chars:
        raise HTTPException(
            status_code=422,
            detail=f"a claim must be {settings.tip_min_chars}-{settings.tip_max_chars} characters")
    msg_id = f"tip_{uuid.uuid4().hex}"
    producer: AIOKafkaProducer = state["producer"]
    await producer.send_and_wait(settings.kafka_output_topic, _message(msg_id, text, "public_tip"))
    logger.info("Queued public tip %s", msg_id)
    return {"reference": msg_id, "status": "queued"}
