from __future__ import annotations
import asyncio
import datetime as _dt
import hashlib
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from storage import OffChainStore
try:
    from blockchain import FabricBridge, FabricGatewayBridge
    from report import make_report, content_hash, verify_report_integrity
except Exception as exc:
    print(f"WARNING: could not import core modules: {exc}")
    FabricBridge = None
    FabricGatewayBridge = None
OFFCHAIN_DB = str(Path(__file__).resolve().parent / "offchain.db")
STORE = None

# Ledger transport selection: auto (default) prefers the official Gateway SDK
# sidecar and falls back to the peer CLI bridge; 'gateway' / 'cli' force one.
FABRIC_BACKEND = os.environ.get("FABRIC_BACKEND", "auto").strip().lower()
FABRIC_GATEWAY_URL = os.environ.get("FABRIC_GATEWAY_URL", "http://localhost:9100")
_gateway_available: Optional[bool] = None


class OrgApply(BaseModel):
    org: str


class AdmissionVote(BaseModel):
    verdict: str


class ReportCreate(BaseModel):
    report_id: str
    language: str
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str
    raw_text: str
    row_id: str = ""
    source_type: str = "tweet"
    source_platform: str = "twitter"
    source_url: str = ""
    published_at: str = ""
    inference_timestamp: str = ""
    submission_type: str = "ai_model"


class ReportVote(BaseModel):
    verdict: str


def require_org(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    org = STORE.org_for_key(x_api_key)
    if not org:
        raise HTTPException(status_code=401, detail="unknown API key")
    return org


def _gateway_up() -> bool:
    global _gateway_available
    if _gateway_available is None:
        _gateway_available = (
            FabricGatewayBridge is not None
            and FabricGatewayBridge.healthy(FABRIC_GATEWAY_URL)
        )
        if _gateway_available:
            print(f"[bridge] using official Fabric Gateway SDK service at {FABRIC_GATEWAY_URL}")
        else:
            print("[bridge] Gateway SDK service unreachable — falling back to peer CLI")
    return _gateway_available


def bridge_for(org: str, endorsers: Optional[List[str]] = None) -> Any:
    if FABRIC_BACKEND in ("gateway", "auto") and FabricGatewayBridge is not None:
        if FABRIC_BACKEND == "gateway" or _gateway_up():
            return FabricGatewayBridge(gateway_url=FABRIC_GATEWAY_URL, org=org,
                                       endorsers=endorsers or ["org1", "org2"])
    if FABRIC_BACKEND == "gateway":
        raise HTTPException(
            status_code=503,
            detail=f"Fabric Gateway SDK service not reachable at {FABRIC_GATEWAY_URL} "
                   "(start it: blockchain/scripts/start-gateway-service.sh)",
        )
    if FabricBridge is None:
        raise HTTPException(status_code=500, detail="bridge unavailable (peer CLI not installed?)")
    return FabricBridge(org=org, endorsers=endorsers or ["org1", "org2"])


def _now_rfc3339() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _on_chain_record(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip().strip('"')
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        numbers = [int(tok) for tok in text.split(",") if tok.strip().lstrip("-").isdigit()]
        if numbers:
            try:
                return json.loads("".join(chr(n) for n in numbers))
            except json.JSONDecodeError:
                pass
    return {}



async def expire_overdue_reports(interval_s: int = 3600) -> None:
    while True:
        try:
            bridge = bridge_for("org1")
            for rec in bridge.query_all() or []:
                if rec.get("status") == "PENDING":
                    deadline = rec.get("voting_deadline", "")
                    if deadline and _dt.datetime.fromisoformat(deadline.replace("Z", "+00:00")) < _dt.datetime.now(_dt.timezone.utc):
                        bridge.expire_report(rec["report_id"])
        except Exception as exc:
            print(f"[scheduler] expiry pass failed: {exc}")
        await asyncio.sleep(interval_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global STORE
    STORE = OffChainStore(OFFCHAIN_DB)
    task = asyncio.create_task(expire_overdue_reports())
    yield
    task.cancel()
app = FastAPI(title="Misinformation Monitoring API", version="2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/orgs/apply")
def apply_org(body: OrgApply, _org: str = Depends(require_org)):
    token = hashlib.sha256(f"{body.org}|{time.time_ns()}".encode("utf-8")).digest()
    STORE.verify_onboarding_token(body.org, token)
    raw = f"{body.org}|{time.time_ns()}|{_org}"
    api_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    STORE.upsert_org_key(api_key, body.org)
    return {"org": body.org, "api_key": api_key}


@app.post("/api/orgs/{msp}/admission")
def request_admission(msp: str, _org: str = Depends(require_org)):
    bridge = bridge_for(_org)
    return {"candidate_msp": bridge.request_admission("Org " + msp, "fact-checker")}


@app.post("/api/orgs/{msp}/admission/vote")
def vote_admission(msp: str, body: AdmissionVote, _org: str = Depends(require_org)):
    bridge = bridge_for(_org)
    bridge.vote_on_admission(msp, body.verdict)
    return {"ok": True}


@app.post("/api/orgs/{msp}/admission/finalize")
def finalize_admission(msp: str, _org: str = Depends(require_org)):
    bridge = bridge_for(_org)
    bridge.finalize_admission(msp)
    return {"ok": True}


@app.get("/api/orgs")
def list_orgs(_org: str = Depends(require_org)):
    return bridge_for(_org).list_orgs()


@app.get("/api/status")
def storage_status(_org: str = Depends(require_org)):
    return STORE.ipfs_status()


@app.get("/api/orgs/{msp}/admission")
def get_admission(msp: str, _org: str = Depends(require_org)):
    return bridge_for(_org).query_admission(msp)


@app.post("/api/reports")
def create_report(body: ReportCreate, _org: str = Depends(require_org)):
    if _org not in ("org1", "org2", "org3"):
        raise HTTPException(status_code=400, detail="unknown signing org")
    report = make_report(
        report_id="",
        language=body.language,
        label=body.label,
        confidence=body.confidence,
        model_version=body.model_version,
        raw_text=body.raw_text,
        row_id=body.row_id,
        source_type=body.source_type,
        source_platform=body.source_platform,
        source_url=body.source_url,
        published_at=body.published_at,
        inference_timestamp=body.inference_timestamp or _now_rfc3339(),
        submitted_at=_now_rfc3339(),
        org_mspid={"org1": "Org1MSP", "org2": "Org2MSP", "org3": "Org3MSP"}[_org],
        submission_type=body.submission_type,
    )
    uri = STORE.save_report(report)
    report_id = report["report_id"]
    bridge = bridge_for(_org)
    bridge.submit_report(
        report_id, body.language, report["content_hash"], body.label,
        body.confidence, body.model_version, _now_rfc3339(), uri,
    )
    return {"report_id": report_id, "off_chain_uri": uri, "content_hash": report["content_hash"]}


@app.get("/api/reports/{report_id}")
def get_report(report_id: str, _org: str = Depends(require_org)):
    report = STORE.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found off-chain")
    return report


@app.get("/api/reports/{report_id}/chain")
def get_chain(report_id: str, _org: str = Depends(require_org)):
    return bridge_for(_org).query_report(report_id)


@app.get("/api/reports")
def list_reports(status: Optional[str] = None, language: Optional[str] = None, _org: str = Depends(require_org)):
    records = bridge_for(_org).query_all() or []
    if status:
        records = [r for r in records if r.get("status") == status]
    if language:
        records = [r for r in records if r.get("language") == language]
    return records


@app.post("/api/reports/{report_id}/vote")
def vote_report(report_id: str, body: ReportVote, _org: str = Depends(require_org)):
    bridge_for(_org).cast_vote(report_id, body.verdict)
    return {"ok": True}


@app.post("/api/reports/{report_id}/finalize")
def finalize_report(report_id: str, _org: str = Depends(require_org)):
    bridge_for(_org).finalize_report(report_id)
    return {"ok": True}


@app.post("/api/reports/{report_id}/expire")
def expire_report(report_id: str, _org: str = Depends(require_org)):
    bridge_for(_org).expire_report(report_id)
    return {"ok": True}


@app.get("/api/reports/{report_id}/verify")
def verify_report(report_id: str, _org: str = Depends(require_org)):
    report = STORE.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found off-chain")
    on_chain = _on_chain_record(bridge_for(_org).query_report(report_id))
    off_chain_hash = content_hash({k: v for k, v in report.items() if k != "content_hash"})
    intact = off_chain_hash == report.get("content_hash")
    matches_on_chain = report.get("content_hash") == on_chain.get("content_hash")
    return {
        "report_id": report_id,
        "off_chain_intact": intact,
        "matches_on_chain": matches_on_chain,
        "verified": intact and matches_on_chain,
        "explanation": (
            "The off-chain copy is unmodified AND its hash matches the immutable, "
            "consortium-voted hash stored on the ledger."
        ),
    }


@app.get("/api/reports/{report_id}/history")
def report_history(report_id: str, _org: str = Depends(require_org)):
    return bridge_for(_org).history(report_id)
