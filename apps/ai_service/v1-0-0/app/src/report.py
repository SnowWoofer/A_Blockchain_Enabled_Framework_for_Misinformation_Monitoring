"""Report schema and canonical hashing (v2 §1).

The full Report object lives off-chain (raw text is never sent to the ledger).
The on-chain `content_hash` is computed over the *whole* canonical JSON object
— not just the raw text — so tampering with any field is detectable.

Use the exact same `content_hash()` function on the client when submitting and
anywhere you later re-verify integrity; inconsistent JSON serialization (key
order, whitespace) would otherwise produce different hashes for identical data.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


def canonical_json(obj: Any) -> str:
    """Stable, deterministic JSON serialization for hashing.

    Sorted keys + no extra whitespace + ASCII-safe separators. This must be the
    only serialization used when computing or verifying `content_hash`.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(payload: Any) -> str:
    """SHA-256 hex digest of the canonical JSON of `payload`."""
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
    """Build the full Report object described in v2 §1.2.

    `content_hash` is computed over everything except the hash field itself, so
    mutating any other field shifts the hash.
    """
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
    """Recompute the hash over the stored report and compare to its own field."""
    expected = report.get("content_hash")
    if not expected:
        return False
    recomputed = content_hash({k: v for k, v in report.items() if k != "content_hash"})
    return recomputed == expected