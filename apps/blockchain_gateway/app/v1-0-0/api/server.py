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
    # ingest_id is the claim's identity from claims.raw and is carried through
    # unchanged. It is NOT the report's identifier — that is the IPFS CID,
    # assigned once the document is sealed.
    ingest_id: str
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str
    content: str
    # Required, with no default: the caller knows where the claim came from.
    # Defaulting it silently attributed every claim to twitter.
    source_platform: str
    published_at: str = ""
    inference_timestamp: str = ""


# final_label / outcome are the binary label domain ("0"/"1"); the off-chain
# document spells the finalised result out for readers.
VERIFIED_STATUS = {"0": "Verified_Non_Misinformation", "1": "Verified_Misinformation"}


class FactCheckSubmission(BaseModel):
    # "0" (non-misinformation) or "1" (misinformation) — the same binary
    # domain the model predicts into, so a finalised final_label can be
    # compared directly against inference_label. Enforced by the chaincode.
    outcome: str
    reasoning: str = ""
    support: List[str] = Field(default_factory=list)


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


def _unwrap_chain_error(msg: str) -> str:
    """Peel the transport wrappers off a chaincode rejection.

    A business rejection ("org X has already fact-checked this claim") arrives
    nested three deep: the sidecar's `gateway service error (500): {json}`,
    wrapping `{"ok":false,...,"error":"chaincode response 500, <message>"}`.
    Callers got an unreadable, truncated blob for what is an ordinary and
    expected refusal, so pull the innermost message out.
    """
    text = msg
    brace = text.find("{")
    if brace != -1:
        try:
            payload = json.loads(text[brace:])
            if isinstance(payload, dict) and payload.get("error"):
                text = str(payload["error"])
        except (ValueError, TypeError):
            pass
    marker = "chaincode response 500, "
    if marker in text:
        text = text.split(marker, 1)[1]
    return text.strip() or msg


def chain_call(fn, *args, **kwargs) -> Any:
    """Run a chaincode invoke/query and surface its error message as a 400
    instead of letting it fall through to FastAPI's bare 500 handler."""
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_unwrap_chain_error(str(exc))) from exc


def _now_rfc3339() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# Consortium membership is not fixed at three. Orgs join by channel config
# update plus an on-chain admission vote, and some of them run no peer at all
# (see blockchain/scripts/add-client-org.sh) — a client-only member signs its
# own transactions and is endorsed by the orgs that do run peers. Derive the
# map from ORGS so adding a member is configuration, not a code change.
ORG_MSPID = {
    o.strip(): f"Org{o.strip().removeprefix('org')}MSP"
    for o in os.environ.get("ORGS", "org1,org2,org3").split(",")
    if o.strip()
}


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
                        bridge.expire_report(rec["root_cid"])
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
    if _org not in ORG_MSPID:
        raise HTTPException(status_code=400, detail="unknown signing org")
    report = make_report(
        ingest_id=body.ingest_id,
        label=body.label,
        confidence=body.confidence,
        model_version=body.model_version,
        content=body.content,
        source_platform=body.source_platform,
        published_at=body.published_at,
        inference_timestamp=body.inference_timestamp or _now_rfc3339(),
        submitted_at=_now_rfc3339(),
        org_mspid=ORG_MSPID[_org],
    )
    uri = STORE.save_report(report)
    root_cid = report["root_cid"]
    bridge = bridge_for(_org)
    chain_call(
        bridge.submit_report,
        root_cid, report["inference_hash"], body.label,
        body.confidence, body.model_version, _now_rfc3339(),
    )
    return {"root_cid": root_cid, "inference_hash": report["inference_hash"]}


@app.get("/api/reports/{root_cid}")
def get_report(root_cid: str, _org: str = Depends(require_org)):
    report = STORE.get_report(root_cid)
    if not report:
        raise HTTPException(status_code=404, detail="report not found off-chain")
    return report


@app.get("/api/reports/{root_cid}/chain")
def get_chain(root_cid: str, _org: str = Depends(require_org)):
    return _on_chain_record(chain_call(bridge_for(_org).query_report, root_cid))


@app.get("/api/reports")
def list_reports(status: Optional[str] = None, _org: str = Depends(require_org)):
    records = _on_chain_list(chain_call(bridge_for(_org).query_all))
    if status:
        records = [r for r in records if r.get("status") == status]
    return records


@app.post("/api/reports/{root_cid}/fact-check")
def submit_fact_check(root_cid: str, body: FactCheckSubmission, _org: str = Depends(require_org)):
    if _org not in ORG_MSPID:
        raise HTTPException(status_code=400, detail="unknown signing org")
    report = STORE.get_report(root_cid)
    if not report:
        raise HTTPException(status_code=404, detail="report not found off-chain")
    # IPFS FIRST, then chain — the reverse of the original order, and safe
    # now only because the ledger holds CurrentCID. The old worry was that a
    # rejected invoke would leave an IPFS document showing more fact-checks
    # than the chain; that document is now simply unreferenced, because
    # CurrentCID still points at the previous version and nothing resolves
    # to the orphan. Publishing first is what lets the new CID be anchored
    # by the same invoke that accepts the fact-check, instead of a second
    # write that could fail on its own.
    # Only the outcome goes on-chain — reasoning/support stay off-chain.
    report = {
        **report,
        "fact_check_status": "Under_Review",
        "fact_checks": [
            *report.get("fact_checks", []),
            {
                "checker_msp": ORG_MSPID[_org],
                "outcome": body.outcome,
                "reasoning": body.reasoning,
                "support": body.support,
                "timestamp": _now_rfc3339(),
            },
        ],
    }
    new_cid = STORE.save_report_version(root_cid, report)
    if not new_cid:
        raise HTTPException(
            status_code=503,
            detail="off-chain store unavailable; fact-check not recorded",
        )
    # The ledger still owns every guard that can reject this — one fact-check
    # per org, PENDING/UNDER_REVIEW only — and advances CurrentCID to the
    # version carrying it in the same transaction.
    bridge = bridge_for(_org)
    chain_call(bridge.submit_fact_check, root_cid, body.outcome, new_cid)

    # The chaincode closes the report itself the moment the fact-checks
    # settle — there is no finalise call to make. Read back what that invoke
    # decided: the tally is a pure function of ledger state, so the gateway
    # cannot know in advance whether this check was the closing one.
    chain_record = _on_chain_record(chain_call(bridge.query_report, root_cid))
    if chain_record.get("status") != "FINAL":
        return {
            "ok": True,
            "current_cid": new_cid,
            "status": chain_record.get("status"),
            "round": chain_record.get("round"),
            "required_panel": chain_record.get("required_panel"),
            "checks_this_round": sum(
                1 for f in chain_record.get("fact_checks", [])
                if f.get("round") == chain_record.get("round")
            ),
        }

    # It closed. Publish one more version carrying the consortium's verdict
    # and advance the pointer to it. Two-phase by necessity: this document
    # records the final_label the chaincode itself computed, so its CID
    # cannot exist until that transaction has committed.
    final_label = chain_record.get("final_label", "")
    report = {**report, "fact_check_status": VERIFIED_STATUS.get(final_label, "Verified")}
    final_cid = STORE.save_report_version(root_cid, report)
    if final_cid:
        chain_call(bridge.set_current_cid, root_cid, final_cid)
        new_cid = final_cid
    return {"ok": True, "current_cid": new_cid, "status": "FINAL", "final_label": final_label}


@app.post("/api/reports/{root_cid}/reopen")
def reopen_report(root_cid: str, _org: str = Depends(require_org)):
    """Put a finalised claim back under review at a larger panel.

    The standing verdict is deliberately left in place — on-chain and in the
    off-chain document — for the duration of the new round. Reopening raises
    a question; it does not withdraw the consortium's answer while that
    question is open."""
    bridge = bridge_for(_org)
    chain_call(bridge.reopen_report, root_cid)
    rec = _on_chain_record(chain_call(bridge.query_report, root_cid))
    return {
        "ok": True,
        "status": rec.get("status"),
        "round": rec.get("round"),
        "required_panel": rec.get("required_panel"),
        "standing_label": rec.get("final_label"),
        "deadline": rec.get("fact_check_deadline"),
        # Full challenge history, readable without walking block history —
        # this is the record a consortium inspects when it suspects bad faith.
        "reopens": rec.get("reopens", []),
    }


@app.post("/api/reports/{root_cid}/expire")
def expire_report(root_cid: str, _org: str = Depends(require_org)):
    chain_call(bridge_for(_org).expire_report, root_cid)
    return {"ok": True}


@app.get("/api/reports/{root_cid}/verify")
def verify_report(root_cid: str, _org: str = Depends(require_org)):
    report = STORE.get_report(root_cid)
    if not report:
        raise HTTPException(status_code=404, detail="report not found off-chain")
    on_chain = _on_chain_record(chain_call(bridge_for(_org).query_report, root_cid))
    intact = verify_report_integrity(report)
    matches_on_chain = report.get("inference_hash") == on_chain.get("inference_hash")
    return {
        "root_cid": root_cid,
        "off_chain_intact": intact,
        "matches_on_chain": matches_on_chain,
        "verified": intact and matches_on_chain,
        "explanation": (
            "The off-chain copy is unmodified AND its hash matches the immutable, "
            "consortium-voted hash stored on the ledger."
        ),
    }


@app.get("/api/reports/{root_cid}/history")
def report_history(root_cid: str, _org: str = Depends(require_org)):
    return _on_chain_list(chain_call(bridge_for(_org).history, root_cid))
