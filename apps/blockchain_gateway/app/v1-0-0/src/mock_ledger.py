"""In-memory stand-in for the misinformation chaincode, for the test suite and
the UI demo mode (FABRIC_BACKEND=mock) only.

It mirrors the consensus and ABAC rules of
blockchain/chaincode/misinformation/go/misinformation.go so the gateway, the
fact-checking service and the web client can be exercised on a laptop without
the Fabric network. It is NOT a ledger: nothing is endorsed, ordered,
replicated or tamper-evident, and /api/public/info reports
`ledger_backend: "mock"` so the UI can say so. Keep the rules here in step with
the chaincode whenever either changes."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

STATUS_PENDING = "PENDING"
STATUS_UNDER_REVIEW = "UNDER_REVIEW"
STATUS_FINAL = "FINAL"
STATUS_REJECTED = "REJECTED"
STATUS_EXPIRED = "EXPIRED"
OPEN_STATUSES = (STATUS_PENDING, STATUS_UNDER_REVIEW)

DEFAULT_WINDOW_S = 72 * 3600


class ChaincodeError(RuntimeError):
    pass


def quorum_for(registered: int) -> int:
    return (2 * registered + 2) // 3 if registered > 0 else 0


def _rfc3339(ts: _dt.datetime) -> str:
    return ts.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _msp(org: str) -> str:
    return f"Org{org.removeprefix('org')}MSP"


class MockLedger:
    def __init__(
        self,
        orgs: List[str],
        window_s: int = DEFAULT_WINDOW_S,
        state_path: Optional[str] = None,
    ) -> None:
        self._lock = threading.RLock()
        self.window_s = window_s
        self.state_path = state_path
        self.orgs: Dict[str, str] = {}
        self.reports: Dict[str, Dict[str, Any]] = {}
        self.history: Dict[str, List[Dict[str, Any]]] = {}
        self.admissions: Dict[str, Dict[str, Any]] = {}
        self._tx = 0
        self._load()
        for org in orgs:
            self.orgs.setdefault(_msp(org), _rfc3339(self._now()))
        self._save()

    # -- plumbing ------------------------------------------------------------

    def _now(self) -> _dt.datetime:
        return _dt.datetime.now(_dt.timezone.utc)

    def _txid(self) -> str:
        self._tx += 1
        return hashlib.sha256(f"{self._tx}|{time.time_ns()}".encode()).hexdigest()

    def _load(self) -> None:
        if self.state_path and Path(self.state_path).exists():
            data = json.loads(Path(self.state_path).read_text())
            self.orgs = data.get("orgs", {})
            self.reports = data.get("reports", {})
            self.history = data.get("history", {})
            self.admissions = data.get("admissions", {})
            self._tx = data.get("tx", 0)

    def _save(self) -> None:
        if not self.state_path:
            return
        tmp = f"{self.state_path}.tmp"
        Path(tmp).write_text(json.dumps({
            "orgs": self.orgs, "reports": self.reports, "history": self.history,
            "admissions": self.admissions, "tx": self._tx,
        }))
        os.replace(tmp, self.state_path)

    def _require_registered(self, msp: str) -> None:
        if msp not in self.orgs:
            raise ChaincodeError(f"org {msp} is not a registered stakeholder; call RegisterOrg first")

    @staticmethod
    def _require_role(role: str, *allowed: str) -> None:
        if role not in allowed:
            raise ChaincodeError(f"identity has role {role!r}; required one of {list(allowed)}")

    def _report(self, report_id: str) -> Dict[str, Any]:
        rec = self.reports.get(report_id)
        if rec is None:
            raise ChaincodeError(f"no report found for {report_id}")
        return rec

    def _write(self, report_id: str, rec: Dict[str, Any], tx_id: str) -> None:
        self.reports[report_id] = rec
        self.history.setdefault(report_id, []).append({
            "tx_id": tx_id, "timestamp": _rfc3339(self._now()), "is_delete": False,
            "record": json.loads(json.dumps(rec)),
        })
        self._save()

    def _past_deadline(self, rec: Dict[str, Any]) -> bool:
        return self._now() > _parse(rec["fact_check_deadline"])

    # -- chaincode functions -------------------------------------------------

    def list_orgs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{"mspid": m, "registered_at": at} for m, at in sorted(self.orgs.items())]

    def submit(self, msp: str, role: str, report_id: str, content_hash: str, label: str,
               confidence: float, model_version: str, timestamp: str) -> str:
        with self._lock:
            if not report_id.strip():
                raise ChaincodeError("report_id must not be empty")
            if label not in ("0", "1"):
                raise ChaincodeError(f'label must be "0" or "1", got {label!r}')
            if not 0 <= float(confidence) <= 1:
                raise ChaincodeError(f"confidence must be in [0,1], got {confidence}")
            if len(content_hash) != 64:
                raise ChaincodeError("content_hash must be a 64-char sha256 hex digest")
            self._require_registered(msp)
            self._require_role(role, "official", "fact_checker")
            if report_id in self.reports:
                raise ChaincodeError(f"report {report_id} already exists (immutable once finalised)")
            tx = self._txid()
            self._write(report_id, {
                "id": report_id, "content_hash": content_hash, "inference_label": label,
                "confidence": float(confidence), "model_version": model_version,
                "timestamp": timestamp, "submitted_by": msp,
                "fact_check_deadline": _rfc3339(self._now() + _dt.timedelta(seconds=self.window_s)),
                "status": STATUS_PENDING, "fact_checks": [],
            }, tx)
            return tx

    def submit_fact_check(self, msp: str, role: str, report_id: str, outcome: str) -> str:
        with self._lock:
            self._require_registered(msp)
            self._require_role(role, "official", "fact_checker")
            if outcome not in ("0", "1"):
                raise ChaincodeError(f'outcome must be "0" or "1", got {outcome!r}')
            rec = json.loads(json.dumps(self._report(report_id)))
            if rec["status"] not in OPEN_STATUSES:
                raise ChaincodeError(
                    f"report {report_id} is {rec['status']}; only PENDING/UNDER_REVIEW reports accept fact-checks")
            if msp == rec["submitted_by"]:
                raise ChaincodeError(
                    f"org {msp} submitted report {report_id} and cannot fact-check it (conflict of interest)")
            if any(fc["checker_msp"] == msp for fc in rec["fact_checks"]):
                raise ChaincodeError(f"org {msp} has already fact-checked report {report_id}")
            tx = self._txid()
            rec["fact_checks"].append({"checker_msp": msp, "outcome": outcome, "txid": tx})
            rec["status"] = STATUS_UNDER_REVIEW
            required = quorum_for(len(self.orgs))
            yes = sum(1 for fc in rec["fact_checks"] if fc["outcome"] == "1")
            voted = {fc["checker_msp"] for fc in rec["fact_checks"]}
            remaining = sum(1 for m in self.orgs if m != rec["submitted_by"] and m not in voted)
            if yes >= required:
                rec.update(status=STATUS_FINAL, final_label="1", finalized_by=msp,
                           finalized_at=_rfc3339(self._now()))
            if rec["status"] == STATUS_UNDER_REVIEW and yes + remaining < required:
                rec["status"] = STATUS_REJECTED
            self._write(report_id, rec, tx)
            return tx

    def finalize_report(self, msp: str, role: str, report_id: str) -> str:
        with self._lock:
            self._require_registered(msp)
            self._require_role(role, "official")
            rec = json.loads(json.dumps(self._report(report_id)))
            if rec["status"] not in OPEN_STATUSES:
                raise ChaincodeError(
                    f"report {report_id} is {rec['status']}; only PENDING/UNDER_REVIEW reports can be finalised")
            if not self._past_deadline(rec):
                raise ChaincodeError(
                    f"report {report_id} is still within its fact-check window (deadline "
                    f"{rec['fact_check_deadline']}); consensus is decided by the 2/3 quorum until then")
            checks = rec["fact_checks"]
            if len(checks) < 2:
                raise ChaincodeError(
                    f"report {report_id} has {len(checks)} fact-check(s); at least 2 fact-checks are required to finalise")
            yes = sum(1 for fc in checks if fc["outcome"] == "1")
            no = len(checks) - yes
            if yes == no:
                raise ChaincodeError(
                    f"report {report_id} is tied at {yes}-{no}; a tie-breaking fact-check is required to finalise")
            tx = self._txid()
            rec.update(status=STATUS_FINAL, final_label="1" if yes > no else "0",
                       finalized_by=msp, finalized_at=_rfc3339(self._now()))
            self._write(report_id, rec, tx)
            return tx

    def expire_report(self, msp: str, role: str, report_id: str) -> str:
        with self._lock:
            self._require_registered(msp)
            self._require_role(role, "official")
            rec = json.loads(json.dumps(self._report(report_id)))
            if rec["status"] not in OPEN_STATUSES:
                raise ChaincodeError(
                    f"report {report_id} is {rec['status']}; only PENDING/UNDER_REVIEW reports can expire")
            if not self._past_deadline(rec):
                raise ChaincodeError(
                    f"report {report_id} is still within its fact-check window (deadline {rec['fact_check_deadline']})")
            tx = self._txid()
            rec.update(status=STATUS_EXPIRED, finalized_by=msp, finalized_at=_rfc3339(self._now()))
            self._write(report_id, rec, tx)
            return tx

    def query_report(self, report_id: str) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._report(report_id)))

    def query_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(list(self.reports.values())))

    def query_history(self, report_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            if report_id not in self.history:
                raise ChaincodeError(f"no report found for {report_id}")
            return json.loads(json.dumps(self.history[report_id]))

    def request_admission(self, msp: str, org_name: str, org_type: str) -> str:
        with self._lock:
            if msp in self.orgs:
                raise ChaincodeError(f"org {msp} is already a registered stakeholder")
            if not org_name.strip():
                raise ChaincodeError("org_name must not be empty")
            prev = self.admissions.get(msp)
            if prev and prev["status"] == "PENDING":
                raise ChaincodeError(f"org {msp} already has a pending admission request")
            self.admissions[msp] = {
                "candidate_msp": msp, "org_name": org_name, "org_type": org_type,
                "requested_at": _rfc3339(self._now()), "votes": [], "status": "PENDING",
            }
            self._save()
            return msp

    def vote_on_admission(self, msp: str, role: str, candidate: str, verdict: str) -> str:
        with self._lock:
            self._require_registered(msp)
            self._require_role(role, "official")
            if verdict not in ("0", "1"):
                raise ChaincodeError(f'verdict must be "0" or "1", got {verdict!r}')
            if msp == candidate:
                raise ChaincodeError(f"org {msp} cannot vote on its own admission request")
            req = self.admissions.get(candidate)
            if req is None:
                raise ChaincodeError(f"no admission request found for {candidate}")
            if req["status"] != "PENDING":
                raise ChaincodeError(f"admission request for {candidate} is {req['status']}")
            if any(v["voter_msp"] == msp for v in req["votes"]):
                raise ChaincodeError(f"org {msp} has already voted on {candidate}'s admission")
            tx = self._txid()
            req["votes"].append({"voter_msp": msp, "verdict": verdict, "txid": tx})
            self._save()
            return tx

    def finalize_admission(self, msp: str, role: str, candidate: str) -> str:
        with self._lock:
            self._require_registered(msp)
            self._require_role(role, "official")
            req = self.admissions.get(candidate)
            if req is None:
                raise ChaincodeError(f"no admission request found for {candidate}")
            if req["status"] != "PENDING":
                raise ChaincodeError(f"admission request for {candidate} is {req['status']}")
            needed = quorum_for(len(self.orgs))
            admits = sum(1 for v in req["votes"] if v["verdict"] == "1")
            if admits < needed:
                raise ChaincodeError(
                    f"admission request for {candidate} has {admits} admit vote(s); at least {needed} "
                    f"(of {len(self.orgs)} registered orgs) are required to finalise")
            req.update(status="ADMITTED", finalized_by=msp, finalized_at=_rfc3339(self._now()))
            self.orgs[candidate] = _rfc3339(self._now())
            self._save()
            return self._txid()

    def query_admission(self, candidate: str) -> Dict[str, Any]:
        with self._lock:
            req = self.admissions.get(candidate)
            if req is None:
                raise ChaincodeError(f"no admission request found for {candidate}")
            return json.loads(json.dumps(req))


class MockLedgerBridge:
    """Same method surface as blockchain.FabricGatewayBridge, signing as
    `org` with the ABAC `role` the real sidecar would select."""

    def __init__(self, ledger: MockLedger, org: str = "org1", role: str = "official") -> None:
        self.ledger = ledger
        self.org = org
        self.role = role
        self.msp = _msp(org)

    def register_org(self) -> str:
        return self.msp

    def list_orgs(self) -> Any:
        return self.ledger.list_orgs()

    def request_admission(self, org_name: str, org_type: str) -> str:
        return self.ledger.request_admission(self.msp, org_name, org_type)

    def vote_on_admission(self, candidate_msp: str, verdict: str) -> str:
        return self.ledger.vote_on_admission(self.msp, self.role, candidate_msp, verdict)

    def finalize_admission(self, candidate_msp: str) -> str:
        return self.ledger.finalize_admission(self.msp, self.role, candidate_msp)

    def query_admission(self, candidate_msp: str) -> Any:
        return self.ledger.query_admission(candidate_msp)

    def submit_report(self, report_id: str, content_hash: str, label: str,
                      confidence: float, model_version: str, timestamp: str) -> str:
        return self.ledger.submit(self.msp, self.role, report_id, content_hash, label,
                                  confidence, model_version, timestamp)

    def submit_fact_check(self, report_id: str, outcome: str) -> str:
        return self.ledger.submit_fact_check(self.msp, self.role, report_id, outcome)

    def finalize_report(self, report_id: str) -> str:
        return self.ledger.finalize_report(self.msp, self.role, report_id)

    def expire_report(self, report_id: str) -> str:
        return self.ledger.expire_report(self.msp, self.role, report_id)

    def query_report(self, report_id: str) -> Any:
        return self.ledger.query_report(report_id)

    def query_all(self) -> Any:
        return self.ledger.query_all()

    def history(self, report_id: str) -> Any:
        return self.ledger.query_history(report_id)
