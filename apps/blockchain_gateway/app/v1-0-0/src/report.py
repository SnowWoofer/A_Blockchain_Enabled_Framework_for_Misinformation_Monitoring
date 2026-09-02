from __future__ import annotations
import hashlib
import json
from typing import Any, Dict


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _core_content(report: Dict[str, Any]) -> Dict[str, Any]:
    """The immutable part of a claim: what was flagged, and the AI's initial
    read on it. content_hash is computed over exactly this and nothing else,
    so it stays fixed across every later version of the document — fields
    that legitimately change over time (fact_checks, fact_check_status,
    report_id/CID, submitter) are deliberately excluded."""
    return {
        "source": report.get("source"),
        "verdict": report.get("verdict"),
    }


def make_report(
    *,
    report_id: str,
    label: str,
    confidence: float,
    model_version: str,
    raw_text: str,
    source_platform: str,
    submission_type: str,
    row_id: str = "",
    source_url: str = "",
    published_at: str = "",
    inference_timestamp: str = "",
    submitted_at: str = "",
    org_mspid: str = "",
    schema_version: str = "1.0",
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "report_id": report_id,
        "schema_version": schema_version,
        "source": {
            "platform": source_platform,
            "original_url": source_url or None,
            "raw_text": raw_text,
            "published_at": published_at,
        },
        "verdict": {
            "label": label,
            "confidence": float(confidence),
            "submission_type": submission_type,
            "model_version": model_version,
            "inference_timestamp": inference_timestamp,
        },
        "submitter": {
            "org_mspid": org_mspid,
            "submitted_at": submitted_at,
        },
        # fact_checks accumulates as fact-checkers report; each entry mirrors
        # the corresponding on-chain FactCheck (verdict/reasoning/evidence).
        # The document is re-published (new CID) on every fact-check rather than
        # mutated in place — old CIDs stay resolvable in IPFS, and the chain
        # of off_chain_uri values anchored on-chain over time (retrievable
        # via GET /api/reports/{id}/history) is this claim's version history.
        "fact_check_status": "Pending",
        "fact_checks": [],
    }
    if row_id:
        report["row_id"] = row_id
    report["content_hash"] = content_hash(_core_content(report))
    return report


def verify_report_integrity(report: Dict[str, Any]) -> bool:
    expected = report.get("content_hash")
    if not expected:
        return False
    return content_hash(_core_content(report)) == expected
