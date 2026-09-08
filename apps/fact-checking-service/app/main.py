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


@app.get("/claims/{root_cid}")
async def get_claim(root_cid: str, org: str = Depends(require_org)):
    return await _chain_call(CLIENT.get_claim(org, root_cid))


@app.post("/claims/{root_cid}/fact-check")
async def submit_fact_check(root_cid: str, body: FactCheckSubmit, org: str = Depends(require_org)):
    if body.outcome not in VALID_OUTCOMES:
        raise HTTPException(status_code=400, detail=f"outcome must be one of {VALID_OUTCOMES}")
    result = await _chain_call(
        CLIENT.submit_fact_check(org, root_cid, body.outcome, body.reasoning, body.support)
    )
    logger.info("Fact-check submitted: org=%s id=%s outcome=%s", org, root_cid, body.outcome)
    return result


@app.post("/claims/{root_cid}/reopen")
async def reopen_claim(root_cid: str, org: str = Depends(require_org)):
    """Reopen a finalised claim for re-examination at a larger panel.

    Any registered org may do this — a fast path to close is only defensible
    if verdicts are correctable, so nobody needs permission to raise a
    question. The existing verdict stands while the new round runs."""
    result = await _chain_call(CLIENT.reopen(org, root_cid))
    logger.info("Claim reopened: org=%s id=%s round=%s panel=%s",
                org, root_cid, result.get("round"), result.get("required_panel"))
    return result
