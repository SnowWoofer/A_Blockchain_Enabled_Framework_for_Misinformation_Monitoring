from __future__ import annotations
import hashlib
import json
from typing import Any, Dict


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def make_report(
    *,
    report_id: str,
    language: str,
    label: str,
    confidence: float,
    model_version: str,
    raw_text: str,
    row_id: str = "",
    source_type: str = "tweet",
    source_platform: str = "twitter",
    source_url: str = "",
    published_at: str = "",
    inference_timestamp: str = "",
    submitted_at: str = "",
    org_mspid: str = "",
    schema_version: str = "1.0",
    submission_type: str = "ai_model",
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "report_id": report_id,
        "schema_version": schema_version,
        "language": language,
        "source": {
            "type": source_type,
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
    }
    if row_id:
        report["row_id"] = row_id
    report["content_hash"] = content_hash({k: v for k, v in report.items() if k != "content_hash"})
    return report


def verify_report_integrity(report: Dict[str, Any]) -> bool:
    expected = report.get("content_hash")
    if not expected:
        return False
    recomputed = content_hash({k: v for k, v in report.items() if k != "content_hash"})
    return recomputed == expected
