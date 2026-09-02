import logging
from typing import Any, Coroutine

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .blockchain_client import CLIENT, BlockchainError
from .config import settings
from .schemas import FactCheckSubmit, VALID_OUTCOMES

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title="Fact-Checking Service")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_KEY_TO_ORG = {key: org for org, key in settings.org_api_keys.items()}


@app.on_event("shutdown")
async def _shutdown():
    await CLIENT.aclose()


def require_org(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """Authorizes the caller's org directly by the same API key
    blockchain_gateway itself accepts — no separate account/login layer.
    Who at that org is actually making the call is out of scope here."""
    org = _KEY_TO_ORG.get(x_api_key)
    if not org:
        raise HTTPException(status_code=401, detail="unknown API key")
    return org


async def _chain_call(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        return await coro
    except BlockchainError as exc:
        raise HTTPException(status_code=exc.status_code if exc.status_code < 500 else 400, detail=exc.detail)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/claims/pending")
async def list_pending(org: str = Depends(require_org)):
    """Claims still open that org hasn't already reported on."""
    return await _chain_call(CLIENT.list_pending_for_org(org))


@app.get("/claims/{report_id}")
async def get_claim(report_id: str, org: str = Depends(require_org)):
    return await _chain_call(CLIENT.get_claim(org, report_id))


@app.post("/claims/{report_id}/fact-check")
async def submit_fact_check(report_id: str, body: FactCheckSubmit, org: str = Depends(require_org)):
    if body.outcome not in VALID_OUTCOMES:
        raise HTTPException(status_code=400, detail=f"outcome must be one of {VALID_OUTCOMES}")
    result = await _chain_call(
        CLIENT.submit_fact_check(org, report_id, body.outcome, body.reasoning, body.support)
    )
    logger.info("Fact-check submitted: org=%s id=%s outcome=%s", org, report_id, body.outcome)
    return result


@app.post("/claims/{report_id}/finalize")
async def finalize_claim(report_id: str, org: str = Depends(require_org)):
    return await _chain_call(CLIENT.finalize(org, report_id))
