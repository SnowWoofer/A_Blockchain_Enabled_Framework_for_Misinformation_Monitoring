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
    from report import make_report, verify_report_integrity
except Exception as exc:
    print(f"WARNING: could not import core modules: {exc}")
    FabricBridge = None
    FabricGatewayBridge = None
OFFCHAIN_DB = os.environ.get("OFFCHAIN_DB") or str(Path(__file__).resolve().parent / "offchain.db")
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
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str
    raw_text: str
    # Required, with no default: the caller knows where the claim came from and
    # how it was judged. Defaulting these silently attributed every claim to
    # twitter/ai_model regardless of its real origin.
    source_platform: str
    submission_type: str
    row_id: str = ""
    source_url: str = ""
    published_at: str = ""
    inference_timestamp: str = ""


class FactCheckSubmission(BaseModel):
    verdict: str  # one of: factual / opinion / misinformation
    reasoning: str = ""
    evidence: List[str] = Field(default_factory=list)


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


def chain_call(fn, *args, **kwargs) -> Any:
    """Run a chaincode invoke/query and surface its error message as a 400
    instead of letting it fall through to FastAPI's bare 500 handler."""
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _now_rfc3339() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


ORG_MSPID = {"org1": "Org1MSP", "org2": "Org2MSP", "org3": "Org3MSP"}


def _decode_chain_value(value: Any) -> Any:
    """The Fabric Gateway SDK sidecar occasionally hands back a query result
    as its raw byte values (a Uint8Array, not a Node Buffer, rendered via
    default toString() as comma-separated decimal codes) instead of decoded
    JSON — non-deterministically, depending on peer/connection path. Recover
    the real value in either case."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip().strip('"')
        if not text:
            return None
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
    return None


def _on_chain_record(value: Any) -> Dict[str, Any]:
    decoded = _decode_chain_value(value)
    return decoded if isinstance(decoded, dict) else {}


def _on_chain_list(value: Any) -> List[Dict[str, Any]]:
    decoded = _decode_chain_value(value)
    if isinstance(decoded, list):
        return [_on_chain_record(item) for item in decoded]
    return []



async def expire_overdue_reports(interval_s: int = 3600) -> None:
    while True:
        try:
            bridge = bridge_for("org1")
            for rec in _on_chain_list(bridge.query_all()):
                if rec.get("status") == "PENDING":
                    deadline = rec.get("fact_check_deadline", "")
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
    return {"candidate_msp": chain_call(bridge.request_admission, "Org " + msp, "fact-checker")}


@app.post("/api/orgs/{msp}/admission/vote")
def vote_admission(msp: str, body: AdmissionVote, _org: str = Depends(require_org)):
    bridge = bridge_for(_org)
    chain_call(bridge.vote_on_admission, msp, body.verdict)
    return {"ok": True}


@app.post("/api/orgs/{msp}/admission/finalize")
def finalize_admission(msp: str, _org: str = Depends(require_org)):
    bridge = bridge_for(_org)
    chain_call(bridge.finalize_admission, msp)
    return {"ok": True}


@app.get("/api/orgs")
def list_orgs(_org: str = Depends(require_org)):
    return _on_chain_list(chain_call(bridge_for(_org).list_orgs))


@app.get("/api/status")
def storage_status(_org: str = Depends(require_org)):
    return STORE.ipfs_status()


@app.get("/api/orgs/{msp}/admission")
def get_admission(msp: str, _org: str = Depends(require_org)):
    return _on_chain_record(chain_call(bridge_for(_org).query_admission, msp))


@app.post("/api/reports")
def create_report(body: ReportCreate, _org: str = Depends(require_org)):
    if _org not in ("org1", "org2", "org3"):
        raise HTTPException(status_code=400, detail="unknown signing org")
    report = make_report(
        report_id="",
        label=body.label,
        confidence=body.confidence,
        model_version=body.model_version,
        raw_text=body.raw_text,
        source_platform=body.source_platform,
        submission_type=body.submission_type,
        row_id=body.row_id,
        source_url=body.source_url,
        published_at=body.published_at,
        inference_timestamp=body.inference_timestamp or _now_rfc3339(),
        submitted_at=_now_rfc3339(),
        org_mspid=ORG_MSPID[_org],
    )
    uri = STORE.save_report(report)
    report_id = report["report_id"]
    bridge = bridge_for(_org)
    chain_call(
        bridge.submit_report,
        report_id, report["content_hash"], body.label,
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
    return _on_chain_record(chain_call(bridge_for(_org).query_report, report_id))


@app.get("/api/reports")
def list_reports(status: Optional[str] = None, _org: str = Depends(require_org)):
    records = _on_chain_list(chain_call(bridge_for(_org).query_all))
    if status:
        records = [r for r in records if r.get("status") == status]
    return records


@app.post("/api/reports/{report_id}/fact-check")
def submit_fact_check(report_id: str, body: FactCheckSubmission, _org: str = Depends(require_org)):
    if _org not in ORG_MSPID:
        raise HTTPException(status_code=400, detail="unknown signing org")
    report = STORE.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found off-chain")
    # Re-publish the full document as a new, immutable version (new CID)
    # with this fact-check appended — old versions stay resolvable in IPFS,
    # nothing is mutated in place. The on-chain SubmitFactCheck call below
    # advances the ledger's off_chain_uri pointer to this new CID.
    report = {
        **report,
        "fact_check_status": "Under_Review",
        "fact_checks": [
            *report.get("fact_checks", []),
            {
                "checker": ORG_MSPID[_org],
                "verdict": body.verdict,
                "reasoning": body.reasoning,
                "evidence": body.evidence,
                "timestamp": _now_rfc3339(),
            },
        ],
    }
    new_uri = STORE.save_report_version(report_id, report)
    # Only the verdict + new CID go on-chain — reasoning/evidence already
    # live in the off-chain document just published above.
    chain_call(bridge_for(_org).submit_fact_check, report_id, body.verdict, new_uri)
    return {"ok": True, "off_chain_uri": new_uri}


@app.post("/api/reports/{report_id}/finalize")
def finalize_report(report_id: str, _org: str = Depends(require_org)):
    chain_call(bridge_for(_org).finalize_report, report_id)
    chain_record = _on_chain_record(chain_call(bridge_for(_org).query_report, report_id))
    final_label = chain_record.get("final_label", "")
    report = STORE.get_report(report_id)
    if report and final_label:
        report = {**report, "fact_check_status": f"Verified_{final_label.capitalize()}"}
        STORE.save_report_version(report_id, report)
    return {"ok": True}


@app.post("/api/reports/{report_id}/expire")
def expire_report(report_id: str, _org: str = Depends(require_org)):
    chain_call(bridge_for(_org).expire_report, report_id)
    return {"ok": True}


@app.get("/api/reports/{report_id}/verify")
def verify_report(report_id: str, _org: str = Depends(require_org)):
    report = STORE.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found off-chain")
    on_chain = _on_chain_record(chain_call(bridge_for(_org).query_report, report_id))
    intact = verify_report_integrity(report)
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
    return _on_chain_list(chain_call(bridge_for(_org).history, report_id))
