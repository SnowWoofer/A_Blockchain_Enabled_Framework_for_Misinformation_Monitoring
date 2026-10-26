from __future__ import annotations
import asyncio
from typing import Any, Dict, List, Optional

import httpx

from .config import settings

OPEN_STATUSES = ("PENDING", "UNDER_REVIEW")


class BlockchainError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def quorum_for(registered: int) -> int:
    # Same rule as the chaincode: ceil(2/3 x registered orgs).
    return (2 * registered + 2) // 3 if registered > 0 else 0


class BlockchainClient:
    """Thin client for apps/blockchain_gateway — the only thing this service
    talks to. It forwards the caller's own credential on every call, so the
    gateway (and through it the chaincode) sees the real caller."""

    def __init__(self):
        self.http = httpx.AsyncClient(base_url=settings.blockchain_api_base, timeout=20.0)

    async def aclose(self):
        await self.http.aclose()

    async def request(self, method: str, creds: Dict[str, str], path: str, **kwargs) -> Any:
        try:
            resp = await self.http.request(method, path, headers=creds, **kwargs)
        except httpx.HTTPError as exc:
            raise BlockchainError(502, f"blockchain gateway unreachable: {exc}") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise BlockchainError(resp.status_code, detail)
        return resp.json()

    async def whoami(self, creds: Dict[str, str]) -> Dict[str, Any]:
        return await self.request("GET", creds, "/api/auth/me")

    async def registered_orgs(self, creds: Dict[str, str]) -> int:
        return len(await self.request("GET", creds, "/api/orgs"))

    async def _document(self, creds: Dict[str, str], report_id: str) -> Dict[str, Any]:
        try:
            return await self.request("GET", creds, f"/api/reports/{report_id}")
        except BlockchainError:
            return {}

    @staticmethod
    def project(record: Dict[str, Any], doc: Dict[str, Any], registered: int, viewer_msp: str) -> Dict[str, Any]:
        """A claim as a fact-checker sees it: the on-chain record joined with
        the readable content of its off-chain document.

        Blind review: until the viewer's org has cast its vote (or the report
        has closed), other orgs' verdicts, reasoning and the yes/no tally are
        withheld; only how many orgs have voted is shown. This keeps each
        org's assessment independent. It is a property of this workflow, not
        of the ledger, which any member can still query directly."""
        source = doc.get("source", {})
        checks = record.get("fact_checks") or []
        submitter = record.get("submitted_by", "")
        voted = {c.get("checker_msp") for c in checks}
        status = record.get("status", "")
        is_open = status in OPEN_STATUSES
        my_vote = next((c for c in checks if c.get("checker_msp") == viewer_msp), None)
        reveal = not is_open or my_vote is not None
        eligible = registered  # every registered org votes, the submitter included
        claim: Dict[str, Any] = {
            "id": record.get("id"),
            "content": source.get("content", "(off-chain content unavailable)"),
            "source_platform": source.get("platform"),
            "published_at": source.get("published_at"),
            "msg_id": doc.get("msg_id"),
            "inference_label": record.get("inference_label"),
            "confidence": record.get("confidence"),
            "model_version": record.get("model_version"),
            "submitted_by": submitter,
            "submitted_at": record.get("timestamp"),
            "status": status,
            "fact_check_deadline": record.get("fact_check_deadline"),
            "consensus": {
                "registered_orgs": registered,
                "required_yes": quorum_for(registered),
                "eligible_voters": eligible,
                "votes_cast": len(checks),
                "remaining_voters": max(eligible - len(voted), 0),
            },
            "my_org_is_submitter": viewer_msp == submitter,
            "my_org_vote": my_vote.get("outcome") if my_vote else None,
            "can_vote": is_open and my_vote is None,
            "blind": not reveal,
        }
        if reveal:
            yes = sum(1 for c in checks if c.get("outcome") == "1")
            claim["consensus"].update(yes=yes, no=len(checks) - yes)
            claim["final_label"] = record.get("final_label")
            claim["finalized_at"] = record.get("finalized_at")
        return claim

    async def _enrich(self, creds: Dict[str, str], records: List[Dict[str, Any]], registered: int,
                      viewer_msp: str) -> List[Dict[str, Any]]:
        sem = asyncio.Semaphore(settings.enrich_concurrency)

        async def one(record: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                doc = await self._document(creds, record["id"])
            return self.project(record, doc, registered, viewer_msp)

        return list(await asyncio.gather(*(one(r) for r in records)))

    async def queue(self, creds: Dict[str, str], viewer_msp: str, *, limit: int, offset: int,
                    sort: str, ai_label: Optional[str]) -> Dict[str, Any]:
        """Open claims this org has not voted on yet, its own submissions
        included. Most urgent (earliest deadline) first by default."""
        records, registered = await asyncio.gather(
            self.request("GET", creds, "/api/reports"), self.registered_orgs(creds))
        todo = [
            r for r in records
            if r.get("status") in OPEN_STATUSES
            and all(c.get("checker_msp") != viewer_msp for c in r.get("fact_checks") or [])
            and (ai_label is None or r.get("inference_label") == ai_label)
        ]
        if sort == "confidence":
            todo.sort(key=lambda r: r.get("confidence") or 0.0, reverse=True)
        elif sort == "newest":
            todo.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
        else:
            todo.sort(key=lambda r: r.get("fact_check_deadline") or "")
        page = todo[offset: offset + limit]
        return {"total": len(todo), "items": await self._enrich(creds, page, registered, viewer_msp)}

    async def reviewed(self, creds: Dict[str, str], viewer_msp: str, *, limit: int, offset: int) -> Dict[str, Any]:
        records, registered = await asyncio.gather(
            self.request("GET", creds, "/api/reports"), self.registered_orgs(creds))
        mine = [r for r in records if any(c.get("checker_msp") == viewer_msp for c in r.get("fact_checks") or [])]
        mine.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
        page = mine[offset: offset + limit]
        return {"total": len(mine), "items": await self._enrich(creds, page, registered, viewer_msp)}

    async def claim(self, creds: Dict[str, str], viewer_msp: str, report_id: str) -> Dict[str, Any]:
        record, doc, registered = await asyncio.gather(
            self.request("GET", creds, f"/api/reports/{report_id}/chain"),
            self._document(creds, report_id),
            self.registered_orgs(creds),
        )
        claim = self.project(record, doc, registered, viewer_msp)
        if claim["blind"]:
            claim["fact_checks"] = []
        else:
            reasons = {fc.get("checker_msp"): fc for fc in doc.get("fact_checks", [])}
            claim["fact_checks"] = [
                {
                    "checker_msp": c.get("checker_msp"),
                    "outcome": c.get("outcome"),
                    "tx_id": c.get("txid"),
                    "reasoning": reasons.get(c.get("checker_msp"), {}).get("reasoning", ""),
                    "support": reasons.get(c.get("checker_msp"), {}).get("support", []),
                    "timestamp": reasons.get(c.get("checker_msp"), {}).get("timestamp", ""),
                }
                for c in record.get("fact_checks") or []
            ]
        return claim

    async def submit_fact_check(self, creds: Dict[str, str], report_id: str, outcome: str, reasoning: str,
                                support: List[str]) -> Dict[str, Any]:
        return await self.request(
            "POST", creds, f"/api/reports/{report_id}/fact-check",
            json={"outcome": outcome, "reasoning": reasoning, "support": support},
        )

    async def finalize(self, creds: Dict[str, str], report_id: str) -> Dict[str, Any]:
        return await self.request("POST", creds, f"/api/reports/{report_id}/finalize")


CLIENT = BlockchainClient()
