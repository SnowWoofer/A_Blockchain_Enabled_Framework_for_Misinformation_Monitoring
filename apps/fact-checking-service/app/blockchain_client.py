from __future__ import annotations
import asyncio
from typing import Any, Dict, List

import httpx

from .config import settings

ORG_MSPID = {"org1": "Org1MSP", "org2": "Org2MSP", "org3": "Org3MSP"}

OPEN_STATUSES = ("PENDING", "UNDER_REVIEW")

# Matches flagging-engine's id2label (config.json: {"0": "non-misinformation",
# "1": "misinformation"}) — the on-chain record only ever carries the raw "0"/
# "1" (that's the actual chaincode-validated value), never a human label.
AI_LABEL = {"0": "not misinformation", "1": "misinformation"}


class BlockchainError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class BlockchainClient:
    """Thin client for apps/blockchain_gateway — the only thing this service
    talks to. Every call signs as the caller's org (blockchain_gateway
    resolves the actual Fabric identity from that org's API key); which
    individual fact-checker made the call is known here, not there."""

    def __init__(self):
        self.http = httpx.AsyncClient(base_url=settings.blockchain_api_base, timeout=20.0)

    async def aclose(self):
        await self.http.aclose()

    def _headers(self, org: str) -> Dict[str, str]:
        key = settings.org_api_keys.get(org)
        if not key:
            raise BlockchainError(500, f"no blockchain API key configured for org '{org}'")
        return {"X-API-Key": key}

    async def _request(self, method: str, org: str, path: str, **kwargs) -> Any:
        resp = await self.http.request(method, path, headers=self._headers(org), **kwargs)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise BlockchainError(resp.status_code, str(detail))
        return resp.json()

    async def list_pending_for_org(self, org: str) -> List[Dict[str, Any]]:
        """Claims still open (PENDING/UNDER_REVIEW) that this org hasn't
        already fact-checked. Membership/status filtering uses the on-chain
        record (it carries the fact_checks[] list needed to check that) — but
        a fact-checker can't fact-check a content_hash, so each candidate is
        enriched with its off-chain document's actual readable content
        (raw_text, source) before being returned."""
        mspid = ORG_MSPID.get(org)
        candidates: List[Dict[str, Any]] = []
        for status in OPEN_STATUSES:
            records = await self._request("GET", org, "/api/reports", params={"status": status})
            for record in records:
                if any(v.get("checker_msp") == mspid for v in record.get("fact_checks", [])):
                    continue
                candidates.append(record)

        async def _enrich(record: Dict[str, Any]) -> Dict[str, Any]:
            try:
                doc = await self.get_claim(org, record["report_id"])
            except BlockchainError:
                doc = {}
            source = doc.get("source", {})
            return {
                "report_id": record.get("report_id"),
                "raw_text": source.get("raw_text", "(off-chain content unavailable)"),
                "source_platform": source.get("platform"),
                "published_at": source.get("published_at"),
                "ai_verdict": AI_LABEL.get(record.get("proposed_label"), record.get("proposed_label")),
                "ai_confidence": record.get("confidence"),
                "model_version": record.get("model_version"),
                "submitted_by": record.get("submitted_by"),
                "status": record.get("status"),
                "fact_checks_so_far": len(record.get("fact_checks", [])),
                "fact_check_deadline": record.get("fact_check_deadline"),
                "off_chain_uri": record.get("off_chain_uri"),
            }

        return list(await asyncio.gather(*(_enrich(r) for r in candidates)))

    async def get_claim(self, org: str, report_id: str) -> Dict[str, Any]:
        """The off-chain document — includes raw claim text and every prior
        fact-check's full reasoning/evidence, which the on-chain record alone
        doesn't carry in as convenient a shape for a reviewer to read."""
        return await self._request("GET", org, f"/api/reports/{report_id}")

    async def submit_report(
        self, org: str, report_id: str, verdict: str, reasoning: str, evidence: List[str],
    ) -> Dict[str, Any]:
        return await self._request(
            "POST", org, f"/api/reports/{report_id}/fact-check",
            json={"verdict": verdict, "reasoning": reasoning, "evidence": evidence},
        )

    async def finalize(self, org: str, report_id: str) -> Dict[str, Any]:
        return await self._request("POST", org, f"/api/reports/{report_id}/finalize")


CLIENT = BlockchainClient()
