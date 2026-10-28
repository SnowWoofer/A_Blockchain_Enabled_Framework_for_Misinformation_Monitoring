import logging
import re
from contextlib import asynccontextmanager
from typing import Any, Coroutine, Dict, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request

from .blockchain_client import CLIENT, BlockchainError
from .schemas import FactCheckSubmit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REPORT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await CLIENT.aclose()


app = FastAPI(title="Fact-Checking Service", lifespan=lifespan)


@app.middleware("http")
async def _headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


class Caller:
    def __init__(self, creds: Dict[str, str], me: Dict[str, Any]):
        self.creds = creds
        self.me = me

    @property
    def mspid(self) -> str:
        return self.me["mspid"]

    @property
    def label(self) -> str:
        return f"{self.me.get('subject')}@{self.me.get('org')}"


async def _chain_call(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        return await coro
    except BlockchainError as exc:
        status = exc.status_code if exc.status_code < 500 else 502
        raise HTTPException(status_code=status, detail=exc.detail)


async def caller(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Caller:
    # Forward the caller's own credential (never one of ours) and let the blockchain gateway say who they are.
    creds: Dict[str, str] = {}
    if authorization:
        creds["Authorization"] = authorization
    elif x_api_key:
        creds["X-API-Key"] = x_api_key
    else:
        raise HTTPException(status_code=401, detail="missing credentials (Authorization: Bearer or X-API-Key)")
    if request.headers.get("x-real-ip"):
        creds["X-Real-IP"] = request.headers["x-real-ip"]
    return Caller(creds, await _chain_call(CLIENT.whoami(creds)))


def _report_id(report_id: str) -> str:
    if not REPORT_ID_RE.fullmatch(report_id):
        raise HTTPException(status_code=400, detail="malformed report id")
    return report_id


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/claims/pending")
async def list_pending(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort: Literal["deadline", "confidence", "newest"] = "deadline",
    ai_label: Optional[Literal["0", "1"]] = None,
    who: Caller = Depends(caller),
):
    #Open claims the caller's org may still vote on.
    return await _chain_call(CLIENT.queue(who.creds, who.mspid, limit=limit, offset=offset,
                                          sort=sort, ai_label=ai_label))


@app.get("/claims/reviewed")
async def list_reviewed(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                        who: Caller = Depends(caller)):
    #Claims the caller's org has already voted on.
    return await _chain_call(CLIENT.reviewed(who.creds, who.mspid, limit=limit, offset=offset))


@app.get("/claims/{report_id}")
async def get_claim(report_id: str, who: Caller = Depends(caller)):
    return await _chain_call(CLIENT.claim(who.creds, who.mspid, _report_id(report_id)))


@app.post("/claims/{report_id}/fact-check")
async def submit_fact_check(report_id: str, body: FactCheckSubmit, who: Caller = Depends(caller)):
    result = await _chain_call(
        CLIENT.submit_fact_check(who.creds, _report_id(report_id), body.outcome, body.reasoning, body.support)
    )
    logger.info("Fact-check submitted: by=%s id=%s outcome=%s", who.label, report_id, body.outcome)
    return result


@app.post("/claims/{report_id}/finalize")
async def finalize_claim(report_id: str, who: Caller = Depends(caller)):
    return await _chain_call(CLIENT.finalize(who.creds, _report_id(report_id)))
