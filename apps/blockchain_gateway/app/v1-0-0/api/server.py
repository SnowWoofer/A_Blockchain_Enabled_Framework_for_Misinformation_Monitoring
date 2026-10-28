from __future__ import annotations
import asyncio
import datetime as _dt
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import audit
import auth
from auth import (
    GOVERN_ROLES, READ_ROLES, ROLES, SUBMIT_ROLES, VOTE_ROLES, Principal,
    get_auth_mode, init_user_store, require, reviewer_ref, set_server_store,
)
from security import (
    MSG_ID_RE, ORG_RE, check_urls, client_ip, install_security_middleware,
    validate_msp, validate_report_id,
)
from storage import OffChainStore
try:
    from blockchain import FabricBridge, FabricGatewayBridge
    from mock_ledger import MockLedger, MockLedgerBridge, quorum_for
    from report import make_report, verify_report_integrity
except Exception as exc:
    print(f"WARNING: could not import core modules: {exc}")
    FabricBridge = None
    FabricGatewayBridge = None
OFFCHAIN_DB = os.environ.get("OFFCHAIN_DB") or str(Path(__file__).resolve().parent / "offchain.db")
STORE = None

# Ledger transport selection: auto (default) prefers the official Gateway SDK
# sidecar and falls back to the peer CLI bridge; 'gateway' / 'cli' force one.
# 'mock' swaps in an in-memory stand-in for UI demos without Fabric.
FABRIC_BACKEND = os.environ.get("FABRIC_BACKEND", "auto").strip().lower()
FABRIC_GATEWAY_URL = os.environ.get("FABRIC_GATEWAY_URL", "http://localhost:9100")
_gateway_available: Optional[bool] = None
_mock_ledger: Optional["MockLedger"] = None

# Content limits. Benchmark payloads are far below these.
MAX_CONTENT_CHARS = 10_000
MAX_REASONING_CHARS = 5_000
MAX_EVIDENCE_LINKS = 10


class OrgApply(BaseModel):
    org: str = Field(..., max_length=16)


class AdmissionVote(BaseModel):
    # "admit"/"reject" are accepted as aliases of the chaincode's "1"/"0".
    verdict: Literal["0", "1", "admit", "reject"]


class ReportCreate(BaseModel):
    # msg_id is the claim's identity from claims.raw and is carried through unchanged.
    msg_id: str = Field(..., min_length=1, max_length=128)
    label: Literal["0", "1"]
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str = Field(..., min_length=1, max_length=128)
    content: str = Field(..., min_length=1, max_length=MAX_CONTENT_CHARS)
    # Required, with no default: the submitter knows where the claim came from.
    source_platform: str = Field(..., min_length=1, max_length=64)
    published_at: str = Field("", max_length=64)
    inference_timestamp: str = Field("", max_length=64)


# final_label / outcome are the binary label domain ("0"/"1");
VERIFIED_STATUS = {"0": "Verified_Non_Misinformation", "1": "Verified_Misinformation"}


class FactCheckSubmission(BaseModel):
    outcome: Literal["0", "1"]  # non-misinformation / misinformation
    reasoning: str = Field("", max_length=MAX_REASONING_CHARS)
    support: List[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_LINKS)

    @field_validator("support")
    @classmethod
    def _links(cls, v: List[str]) -> List[str]:
        return check_urls(u.strip() for u in v)


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
            print("[bridge] Gateway SDK service unreachable, falling back to peer CLI")
    return _gateway_available


def _ledger() -> "MockLedger":
    global _mock_ledger
    if _mock_ledger is None:
        _mock_ledger = MockLedger(
            list(ORG_MSPID),
            window_s=int(os.environ.get("MOCK_FACT_CHECK_WINDOW_S", str(72 * 3600))),
            state_path=os.environ.get("MOCK_LEDGER_STATE") or None,
        )
        print("[bridge] FABRIC_BACKEND=mock: in-memory demo ledger, NOT a blockchain")
    return _mock_ledger


def bridge_for(org: str, role: str = "official", endorsers: Optional[List[str]] = None) -> Any:
    # A ledger client that signs as `org` using the identity for `role`.
    if FABRIC_BACKEND == "mock":
        return MockLedgerBridge(_ledger(), org=org, role=role)
    if FABRIC_BACKEND in ("gateway", "auto") and FabricGatewayBridge is not None:
        if FABRIC_BACKEND == "gateway" or _gateway_up():
            return FabricGatewayBridge(gateway_url=FABRIC_GATEWAY_URL, org=org, role=role,
                                       endorsers=endorsers or ["org1", "org2"])
    if FABRIC_BACKEND == "gateway":
        raise HTTPException(
            status_code=503,
            detail=f"Fabric Gateway SDK service not reachable at {FABRIC_GATEWAY_URL} "
                   "(start it: blockchain/scripts/start-gateway-service.sh)",
        )
    if FabricBridge is None:
        raise HTTPException(status_code=500, detail="bridge unavailable (peer CLI not installed?)")
    return FabricBridge(org=org, role=role, endorsers=endorsers or ["org1", "org2"])


def _bridge(principal: Principal) -> Any:
    return bridge_for(principal.org, principal.fabric_role)


def _chain_error_detail(exc: Exception) -> str:
    # The sidecar wraps chaincode errors as `gateway service error (500):{"ok":false,...,"error":"<message>"}`; surface just the message.
    text = str(exc)
    brace = text.find("{")
    if brace != -1:
        try:
            body = json.loads(text[brace:])
            if isinstance(body, dict) and body.get("error"):
                return str(body["error"])
        except json.JSONDecodeError:
            pass
    return text


def chain_call(fn, *args, **kwargs) -> Any:
    #Run a chaincode invoke/query and surface its error message as a 400
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        detail = _chain_error_detail(exc)
        status = 404 if detail.startswith("no report found") else 400
        raise HTTPException(status_code=status, detail=detail) from exc


def _now_rfc3339() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

# Consortium membership is not fixed at three. Orgs join by channel config update plus an on-chain admission vote, and some of them run no peer at all
ORG_MSPID = {
    o.strip(): f"Org{o.strip().removeprefix('org')}MSP"
    for o in os.environ.get("ORGS", "org1,org2,org3").split(",")
    if o.strip()
}
# Org whose identity runs the hourly expiry pass and the public read API.
SERVICE_ORG = os.environ.get("SERVICE_ORG") or next(iter(ORG_MSPID), "org1")


def _decode_chain_value(value: Any) -> Any:
    # The Fabric Gateway SDK sidecar occasionally hands back a query result as its raw byte values instead of decoded JSON.
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


def _audit(principal: Principal, request: Request, action: str, target: str = "",
           outcome: str = "ok", **details: Any) -> None:
    audit.record(action=action, actor=principal.subject, org=principal.org, role=principal.role,
                 target=target, outcome=outcome, ip=client_ip(request), details=details)


# Off-chain documents are read-modify-written on every vote; concurrent votes from different orgs on the same report would otherwise lose one another's reasoning. Striped locks keep memory bounded however many reports exist.
_DOC_LOCKS = [threading.Lock() for _ in range(64)]


def _doc_lock(report_id: str) -> threading.Lock:
    return _DOC_LOCKS[int(hashlib.sha256(report_id.encode()).hexdigest(), 16) % len(_DOC_LOCKS)]


class _TTLCache:
    # Short-lived cache for the unauthenticated read API, so public traffic cannot turn into one full-ledger scan per request against the peers.

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl_s: float, loader: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < ttl_s:
                return hit[1]
        value = loader()
        with self._lock:
            self._data[key] = (now, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


PUBLIC_CACHE = _TTLCache()
PUBLIC_TTL_S = float(os.environ.get("PUBLIC_CACHE_TTL_S", "5"))


async def expire_overdue_reports(interval_s: int = 3600) -> None:
    while True:
        try:
            bridge = bridge_for(SERVICE_ORG, "official")
            for rec in _on_chain_list(await asyncio.to_thread(bridge.query_all)):
                if rec.get("status") == "PENDING":
                    deadline = rec.get("fact_check_deadline", "")
                    if deadline and _dt.datetime.fromisoformat(deadline.replace("Z", "+00:00")) < _dt.datetime.now(_dt.timezone.utc):
                        await asyncio.to_thread(bridge.expire_report, rec["id"])
                        audit.record(action="report_expired", actor="scheduler", org=SERVICE_ORG,
                                     role="official", target=rec["id"])
                        report = STORE.get_report(rec["id"])
                        if report:
                            report = {**report, "fact_check_status": "Expired"}
                            STORE.save_report_version(rec["id"], report)
        except Exception as exc:
            print(f"[scheduler] expiry pass failed: {exc}")
        await asyncio.sleep(interval_s)

USER_DB = os.environ.get("USER_DB") or str(Path(__file__).resolve().parent / "users.db")
AUDIT_DB = os.environ.get("AUDIT_DB") or str(Path(USER_DB).resolve().parent / "audit.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global STORE
    STORE = OffChainStore(OFFCHAIN_DB)
    set_server_store(STORE)
    init_user_store(USER_DB)
    audit.init_audit(AUDIT_DB)
    task = asyncio.create_task(expire_overdue_reports())
    yield
    task.cancel()


app = FastAPI(title="Misinformation Monitoring API", version="3.0", lifespan=lifespan)
install_security_middleware(app)
# The web client is served from the same origin as the API (via the edge proxy), so CORS stays closed unless specific origins are configured.
_cors = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Requested-With"],
    )
app.include_router(auth.router)

_any_role = require(*ROLES)
_reader = require(*READ_ROLES)
_submitter = require(*SUBMIT_ROLES)
_voter = require(*VOTE_ROLES)
_governor = require(*GOVERN_ROLES)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/api/orgs/apply")
def apply_org(body: OrgApply, request: Request, principal: Principal = Depends(_governor)):
    """Mint an API key for an org that is already registered on-chain. The
    key is returned once and stored only as a hash."""
    if not ORG_RE.fullmatch(body.org):
        raise HTTPException(status_code=400, detail="org must look like org<N>")
    registered = _on_chain_list(chain_call(_bridge(principal).list_orgs))
    registered_msps = {o.get("mspid", "") for o in registered}
    if auth.msp_for(body.org) not in registered_msps:
        raise HTTPException(status_code=400, detail=f"org '{body.org}' is not registered on-chain")
    token = hashlib.sha256(f"{body.org}|{time.time_ns()}".encode("utf-8")).digest()
    STORE.verify_onboarding_token(body.org, token)
    api_key = secrets.token_urlsafe(32)
    STORE.upsert_org_key(api_key, body.org, role="official", label=f"apply:{principal.org}")
    _audit(principal, request, "api_key_issued", target=body.org)
    return {"org": body.org, "api_key": api_key}


@app.post("/api/orgs/{msp}/admission")
def request_admission(msp: str, request: Request, principal: Principal = Depends(_governor)):
    validate_msp(msp)
    candidate = chain_call(_bridge(principal).request_admission, "Org " + msp, "fact-checker")
    _audit(principal, request, "admission_requested", target=msp)
    return {"candidate_msp": candidate}


@app.post("/api/orgs/{msp}/admission/vote")
def vote_admission(msp: str, body: AdmissionVote, request: Request, principal: Principal = Depends(_governor)):
    validate_msp(msp)
    verdict = {"admit": "1", "reject": "0"}.get(body.verdict, body.verdict)
    chain_call(_bridge(principal).vote_on_admission, msp, verdict)
    _audit(principal, request, "admission_vote", target=msp, verdict=verdict)
    return {"ok": True}


@app.post("/api/orgs/{msp}/admission/finalize")
def finalize_admission(msp: str, request: Request, principal: Principal = Depends(_governor)):
    validate_msp(msp)
    chain_call(_bridge(principal).finalize_admission, msp)
    _audit(principal, request, "admission_finalized", target=msp)
    return {"ok": True}


@app.get("/api/orgs")
def list_orgs(principal: Principal = Depends(_reader)):
    return _on_chain_list(chain_call(_bridge(principal).list_orgs))


@app.get("/api/status")
def storage_status(principal: Principal = Depends(_any_role)):
    return {**STORE.ipfs_status(), "ledger_backend": FABRIC_BACKEND, "auth_mode": get_auth_mode()}


@app.get("/api/orgs/{msp}/admission")
def get_admission(msp: str, principal: Principal = Depends(_reader)):
    validate_msp(msp)
    return _on_chain_record(chain_call(_bridge(principal).query_admission, msp))


@app.post("/api/reports")
def create_report(body: ReportCreate, request: Request, principal: Principal = Depends(_submitter)):
    if principal.org not in ORG_MSPID:
        raise HTTPException(status_code=400, detail="unknown signing org")
    report = make_report(
        msg_id=body.msg_id,
        label=body.label,
        confidence=body.confidence,
        model_version=body.model_version,
        content=body.content,
        source_platform=body.source_platform,
        published_at=body.published_at,
        inference_timestamp=body.inference_timestamp or _now_rfc3339(),
        submitted_at=_now_rfc3339(),
        org_mspid=ORG_MSPID[principal.org],
    )
    STORE.save_report(report)
    report_id = report["report_id"]
    chain_call(
        _bridge(principal).submit_report,
        report_id, report["content_hash"], body.label,
        body.confidence, body.model_version, _now_rfc3339(),
    )
    STORE.index_report(report_id, body.msg_id, body.source_platform, body.content,
                       report["submitter"]["submitted_at"])
    _audit(principal, request, "report_submitted", target=report_id, label=body.label)
    return {"id": report_id, "content_hash": report["content_hash"]}


@app.get("/api/reports/{report_id}")
def get_report(report_id: str, principal: Principal = Depends(_reader)):
    validate_report_id(report_id)
    report = STORE.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found off-chain")
    return report


@app.get("/api/reports/{report_id}/chain")
def get_chain(report_id: str, principal: Principal = Depends(_reader)):
    validate_report_id(report_id)
    return _on_chain_record(chain_call(_bridge(principal).query_report, report_id))


@app.get("/api/reports")
def list_reports(status: Optional[str] = Query(None, max_length=16), principal: Principal = Depends(_reader)):
    records = _on_chain_list(chain_call(_bridge(principal).query_all))
    if status:
        records = [r for r in records if r.get("status") == status]
    return records


def consensus_summary(record: Dict[str, Any], registered: int, viewer_msp: str = "") -> Dict[str, Any]:
    # Where a report stands against the chaincode's 2/3 rule. Every registered org votes, the submitter included.
    checks = record.get("fact_checks") or []
    yes = sum(1 for c in checks if c.get("outcome") == "1")
    voted = {c.get("checker_msp") for c in checks}
    submitter = record.get("submitted_by", "")
    eligible = registered
    status = record.get("status", "")
    return {
        "report_id": record.get("id"),
        "status": status,
        "registered_orgs": registered,
        "required_yes": quorum_for(registered),
        "eligible_voters": eligible,
        "votes_cast": len(checks),
        "yes": yes,
        "no": len(checks) - yes,
        "remaining_voters": max(eligible - len(voted), 0),
        "submitted_by": submitter,
        "deadline": record.get("fact_check_deadline", ""),
        "my_org_voted": bool(viewer_msp) and viewer_msp in voted,
        "my_org_is_submitter": bool(viewer_msp) and viewer_msp == submitter,
        "can_vote": (bool(viewer_msp) and status in ("PENDING", "UNDER_REVIEW")
                     and viewer_msp not in voted),
    }


@app.get("/api/reports/{report_id}/consensus")
def get_consensus(report_id: str, principal: Principal = Depends(_reader)):
    validate_report_id(report_id)
    bridge = _bridge(principal)
    record = _on_chain_record(chain_call(bridge.query_report, report_id))
    registered = len(_on_chain_list(chain_call(bridge.list_orgs)))
    return consensus_summary(record, registered, principal.mspid)


@app.post("/api/reports/{report_id}/fact-check")
def submit_fact_check(report_id: str, body: FactCheckSubmission, request: Request,
                      principal: Principal = Depends(_voter)):
    validate_report_id(report_id)
    if principal.org not in ORG_MSPID:
        raise HTTPException(status_code=400, detail="unknown signing org")
    with _doc_lock(report_id):
        report = STORE.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail="report not found off-chain")
        try:
            chain_call(_bridge(principal).submit_fact_check, report_id, body.outcome)
        except HTTPException as exc:
            _audit(principal, request, "fact_check", target=report_id, outcome="rejected", reason=exc.detail)
            raise
        report = {
            **report,
            "fact_check_status": "Under_Review",
            "fact_checks": [
                *report.get("fact_checks", []),
                {
                    "checker_msp": ORG_MSPID[principal.org],
                    "outcome": body.outcome,
                    "reasoning": body.reasoning,
                    "support": body.support,
                    "reviewer_ref": reviewer_ref(principal),
                    "timestamp": _now_rfc3339(),
                },
            ],
        }
        STORE.save_report_version(report_id, report)
    _audit(principal, request, "fact_check", target=report_id, outcome_label=body.outcome)
    PUBLIC_CACHE.clear()
    return {"ok": True}


@app.post("/api/reports/{report_id}/finalize")
def finalize_report(report_id: str, request: Request, principal: Principal = Depends(_governor)):
    validate_report_id(report_id)
    bridge = _bridge(principal)
    chain_call(bridge.finalize_report, report_id)
    chain_record = _on_chain_record(chain_call(bridge.query_report, report_id))
    final_label = chain_record.get("final_label", "")
    with _doc_lock(report_id):
        report = STORE.get_report(report_id)
        if report and final_label:
            report = {**report, "fact_check_status": VERIFIED_STATUS.get(final_label, "Verified")}
            STORE.save_report_version(report_id, report)
    _audit(principal, request, "report_finalized", target=report_id, final_label=final_label)
    PUBLIC_CACHE.clear()
    return {"ok": True}


@app.post("/api/reports/{report_id}/expire")
def expire_report(report_id: str, request: Request, principal: Principal = Depends(_governor)):
    validate_report_id(report_id)
    chain_call(_bridge(principal).expire_report, report_id)
    with _doc_lock(report_id):
        report = STORE.get_report(report_id)
        if report:
            report = {**report, "fact_check_status": "Expired"}
            STORE.save_report_version(report_id, report)
    _audit(principal, request, "report_expired", target=report_id)
    PUBLIC_CACHE.clear()
    return {"ok": True}


def _verification(report_id: str, report: Dict[str, Any], on_chain: Dict[str, Any]) -> Dict[str, Any]:
    intact = verify_report_integrity(report)
    matches_on_chain = bool(on_chain) and report.get("content_hash") == on_chain.get("content_hash")
    if intact and matches_on_chain:
        explanation = ("The off-chain copy is unmodified AND its hash matches the immutable, "
                       "consortium-voted hash stored on the ledger.")
    elif not intact:
        explanation = "The off-chain copy does not hash to its recorded content_hash: it was modified."
    else:
        explanation = "The off-chain copy's hash differs from the hash anchored on the ledger."
    return {
        "report_id": report_id,
        "content_hash": report.get("content_hash"),
        "on_chain_hash": on_chain.get("content_hash"),
        "off_chain_intact": intact,
        "matches_on_chain": matches_on_chain,
        "verified": intact and matches_on_chain,
        "explanation": explanation,
    }


@app.get("/api/reports/{report_id}/verify")
def verify_report(report_id: str, principal: Principal = Depends(_reader)):
    validate_report_id(report_id)
    report = STORE.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found off-chain")
    on_chain = _on_chain_record(chain_call(_bridge(principal).query_report, report_id))
    return _verification(report_id, report, on_chain)


@app.get("/api/reports/{report_id}/history")
def report_history(report_id: str, principal: Principal = Depends(_reader)):
    validate_report_id(report_id)
    return _on_chain_list(chain_call(_bridge(principal).history, report_id))


@app.get("/api/audit")
def audit_entries(
    limit: int = Query(100, ge=1, le=500),
    before_id: Optional[int] = Query(None, ge=1),
    action: Optional[str] = Query(None, max_length=40),
    principal: Principal = Depends(_governor),
):
    log = audit.get_audit()
    if log is None:
        raise HTTPException(status_code=500, detail="audit log not initialized")
    return log.entries(org=principal.org, limit=limit, before_id=before_id, action=action)


@app.get("/api/audit/verify")
def audit_verify(principal: Principal = Depends(_governor)):
    log = audit.get_audit()
    if log is None:
        raise HTTPException(status_code=500, detail="audit log not initialized")
    return log.verify()

# Public, unauthenticated read API for the transparency portal. Only closed reports are exposed: an unverified claim still under review is never published, so the portal cannot amplify misinformation. Fact-checkers appear only as their organisation.

CLOSED_STATUSES = ("FINAL", "REJECTED", "EXPIRED")


def _public_bridge() -> Any:
    return bridge_for(SERVICE_ORG, "fact_checker")


def _public_records() -> List[Dict[str, Any]]:
    return PUBLIC_CACHE.get("all", PUBLIC_TTL_S, lambda: _on_chain_list(chain_call(_public_bridge().query_all)))


def _public_registered() -> int:
    return PUBLIC_CACHE.get("orgs", 30.0, lambda: len(_on_chain_list(chain_call(_public_bridge().list_orgs))))


def _verdict(record: Dict[str, Any]) -> str:
    status = record.get("status")
    if status == "FINAL":
        return "misinformation" if record.get("final_label") == "1" else "not_misinformation"
    return {"REJECTED": "not_confirmed", "EXPIRED": "expired"}.get(status, "under_review")


def _public_summary(record: Dict[str, Any], meta: Optional[Dict[str, Any]], registered: int) -> Dict[str, Any]:
    checks = record.get("fact_checks") or []
    yes = sum(1 for c in checks if c.get("outcome") == "1")
    return {
        "id": record.get("id"),
        "status": record.get("status"),
        "verdict": _verdict(record),
        "claim": (meta or {}).get("content", ""),
        "platform": (meta or {}).get("platform", ""),
        "submitted_at": record.get("timestamp"),
        "closed_at": record.get("finalized_at", ""),
        "ai_label": record.get("inference_label"),
        "ai_confidence": record.get("confidence"),
        "votes": {"cast": len(checks), "yes": yes, "no": len(checks) - yes,
                  "required_yes": quorum_for(registered), "registered_orgs": registered},
    }


def _ensure_meta(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    # Claim text for a page of reports. Reports submitted before the search index existed are back-filled from their off-chain document.
    ids = [r["id"] for r in records if r.get("id")]
    meta = STORE.report_meta(ids)
    for rid in ids:
        if rid not in meta:
            doc = STORE.get_report(rid)
            if doc:
                src = doc.get("source", {})
                STORE.index_report(rid, doc.get("msg_id", ""), src.get("platform", ""),
                                   src.get("content", ""), doc.get("submitter", {}).get("submitted_at", ""))
                meta[rid] = {"content": src.get("content", ""), "platform": src.get("platform", "")}
    return meta


@app.get("/api/public/info")
def public_info():
    registered = _public_registered()
    return {
        "ledger_backend": FABRIC_BACKEND,
        "auth_mode": get_auth_mode(),
        "registered_orgs": registered,
        "required_yes": quorum_for(registered),
        "fact_check_window_hours": 72,
    }


@app.get("/api/public/stats")
def public_stats():
    records = _public_records()
    counts: Dict[str, int] = {}
    for r in records:
        v = _verdict(r)
        counts[v] = counts.get(v, 0) + 1
    return {"total_reports": len(records), "by_verdict": counts, "registered_orgs": _public_registered()}


@app.get("/api/public/reports")
def public_reports(
    q: Optional[str] = Query(None, max_length=200),
    verdict: Optional[Literal["misinformation", "not_misinformation", "not_confirmed", "expired"]] = None,
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(20, ge=1, le=50),
):
    records = [r for r in _public_records() if r.get("status") in CLOSED_STATUSES]
    if verdict:
        records = [r for r in records if _verdict(r) == verdict]
    if q and q.strip():
        rank = {rid: i for i, rid in enumerate(STORE.search_reports(q))}
        records = sorted((r for r in records if r.get("id") in rank), key=lambda r: rank[r["id"]])
    else:
        records.sort(key=lambda r: r.get("finalized_at") or r.get("timestamp") or "", reverse=True)
    total = len(records)
    page_records = records[(page - 1) * page_size: page * page_size]
    meta = _ensure_meta(page_records)
    registered = _public_registered()
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [_public_summary(r, meta.get(r["id"]), registered) for r in page_records],
    }


@app.get("/api/public/reports/{report_id}")
def public_report(report_id: str):
    validate_report_id(report_id)
    record = _on_chain_record(chain_call(_public_bridge().query_report, report_id))
    if record.get("status") not in CLOSED_STATUSES:
        raise HTTPException(status_code=404, detail="no published verdict for this report")
    doc = STORE.get_report(report_id) or {}
    registered = _public_registered()
    meta = {"content": doc.get("source", {}).get("content", ""), "platform": doc.get("source", {}).get("platform", "")}
    reasons = {fc.get("checker_msp"): fc for fc in doc.get("fact_checks", [])}
    history = _on_chain_list(chain_call(_public_bridge().history, report_id))
    return {
        **_public_summary(record, meta, registered),
        "published_at": doc.get("source", {}).get("published_at", ""),
        "model_version": record.get("model_version"),
        "deadline": record.get("fact_check_deadline"),
        "fact_checks": [
            {
                "org": c.get("checker_msp"),
                "outcome": c.get("outcome"),
                "tx_id": c.get("txid"),
                "reasoning": reasons.get(c.get("checker_msp"), {}).get("reasoning", ""),
                "evidence": [u for u in reasons.get(c.get("checker_msp"), {}).get("support", [])
                             if isinstance(u, str)],
                "timestamp": reasons.get(c.get("checker_msp"), {}).get("timestamp", ""),
            }
            for c in record.get("fact_checks") or []
        ],
        "integrity": _verification(report_id, doc, record) if doc else None,
        "history": [{"tx_id": h.get("tx_id"), "timestamp": h.get("timestamp"),
                     "status": (h.get("record") or {}).get("status")} for h in history],
    }


@app.get("/api/public/tips/{msg_id}")
def public_tip_status(msg_id: str):
    # Lets whoever submitted a tip follow it using the reference they got back. The reference is random, so it reveals nothing about other claims.
    if not MSG_ID_RE.fullmatch(msg_id):
        raise HTTPException(status_code=400, detail="malformed reference")
    report_id = STORE.report_id_for_msg(msg_id)
    if not report_id:
        return {"reference": msg_id, "state": "screening"}
    try:
        record = _on_chain_record(chain_call(_public_bridge().query_report, report_id))
    except HTTPException:
        return {"reference": msg_id, "state": "screening"}
    if record.get("status") in CLOSED_STATUSES:
        return {"reference": msg_id, "state": "closed", "report_id": report_id, "verdict": _verdict(record)}
    return {"reference": msg_id, "state": "under_review"}
