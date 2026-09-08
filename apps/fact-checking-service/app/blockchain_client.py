from __future__ import annotations
import asyncio
from typing import Any, Dict, List

import httpx

from .config import settings

# Derived from the configured org keys so a newly admitted member — including
# a client-only one with no peer — needs no code change here.
ORG_MSPID = {
    org: f"Org{org.removeprefix('org')}MSP" for org in settings.org_api_keys
}

# REOPENED claims are the ones that most need checkers — omitting it hid
# every reopened claim from the queue that fact-checkers work from.
OPEN_STATUSES = ("PENDING", "UNDER_REVIEW", "REOPENED")


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

    @staticmethod
    def _project(record: Dict[str, Any], doc: Dict[str, Any]) -> Dict[str, Any]:
        """The claim as a fact-checker sees it: the on-chain record joined with
        the readable content from its off-chain document. GET /claims/{id} and
        GET /claims/pending both return exactly this shape — the single-claim
        view only adds the fact_checks[] detail on top."""
        source = doc.get("source", {})
        return {
            "root_cid": record.get("root_cid"),
            "content": source.get("content", "(off-chain content unavailable)"),
            "source_platform": source.get("platform"),
            "published_at": source.get("published_at"),
            "inference_label": record.get("inference_label"),
            "confidence": record.get("confidence"),
            "model_version": record.get("model_version"),
            "submitted_by": record.get("submitted_by"),
            "status": record.get("status"),
            "fact_checks_so_far": len(record.get("fact_checks", [])),
            "fact_check_deadline": record.get("fact_check_deadline"),
        }

    async def _document(self, org: str, root_cid: str) -> Dict[str, Any]:
        """The raw off-chain IPFS document."""
        return await self._request("GET", org, f"/api/reports/{root_cid}")

    async def list_pending_for_org(self, org: str) -> List[Dict[str, Any]]:
        """Claims still open (PENDING/UNDER_REVIEW/REOPENED) that this org
        hasn't already fact-checked *in the current round*. The chaincode
        allows one check per org per round, not per report, so an org that
        checked in round 0 may — and should — check again after a reopen;
        filtering on the whole fact_checks[] list would hide exactly those
        claims from the orgs best placed to re-examine them.

        Membership/status filtering uses the on-chain record (it carries the
        fact_checks[] list needed to check that) — but a fact-checker can't
        fact-check an inference_hash, so each candidate is enriched with its
        off-chain document's readable content before being returned."""
        mspid = ORG_MSPID.get(org)
        candidates: List[Dict[str, Any]] = []
        for status in OPEN_STATUSES:
            records = await self._request("GET", org, "/api/reports", params={"status": status})
            for record in records:
                current_round = record.get("round", 0)
                if any(
                    v.get("checker_msp") == mspid and v.get("round", 0) == current_round
                    for v in record.get("fact_checks", [])
                ):
                    continue
                candidates.append(record)

        async def _enrich(record: Dict[str, Any]) -> Dict[str, Any]:
            try:
                doc = await self._document(org, record["root_cid"])
            except BlockchainError:
                doc = {}
            return self._project(record, doc)

        return list(await asyncio.gather(*(_enrich(r) for r in candidates)))

    async def get_claim(self, org: str, root_cid: str) -> Dict[str, Any]:
        """One claim in the same shape as /claims/pending, plus fact_checks[] —
        each prior fact-check's full reasoning/support, which the on-chain
        record doesn't carry."""
        record = await self._request("GET", org, f"/api/reports/{root_cid}/chain")
        try:
            doc = await self._document(org, root_cid)
        except BlockchainError:
            doc = {}
        return {**self._project(record, doc), "fact_checks": doc.get("fact_checks", [])}

    async def submit_fact_check(
        self, org: str, root_cid: str, outcome: str, reasoning: str, support: List[str],
    ) -> Dict[str, Any]:
        return await self._request(
            "POST", org, f"/api/reports/{root_cid}/fact-check",
            json={"outcome": outcome, "reasoning": reasoning, "support": support},
        )


    async def reopen(self, org: str, root_cid: str) -> Dict[str, Any]:
        return await self._request("POST", org, f"/api/reports/{root_cid}/reopen")


CLIENT = BlockchainClient()
