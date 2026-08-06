"""Off-chain report storage + API-key -> org mapping (v2 §3.4, §4).

A single SQLite database holds the full Report objects that never touch the
ledger. `off_chain_uri` on-chain points at this store. Chosen to keep the
deployment zero-infrastructure; swap for Postgres/IPFS if you outgrow it.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


class OffChainStore:
    def __init__(self, db_path: str = "offchain.db") -> None:
        parent = os.path.dirname(os.path.abspath(db_path))
        Path(parent).mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS org_keys (
                    api_key TEXT PRIMARY KEY,
                    org      TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    report_id   TEXT PRIMARY KEY,
                    payload     TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                )
                """
            )

    # ---- org keys ---------------------------------------------------------
    def upsert_org_key(self, api_key: str, org: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO org_keys (api_key, org) VALUES (?, ?)",
                (api_key, org),
            )

    def org_for_key(self, api_key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT org FROM org_keys WHERE api_key=?", (api_key,)
        ).fetchone()
        return row["org"] if row else None

    # ---- reports ----------------------------------------------------------
    def save_report(self, report_id: str, report: Dict[str, Any]) -> None:
        payload = json.dumps(report, sort_keys=True)
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO reports (report_id, payload, content_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (report_id, payload, report.get("content_hash", ""), report.get("submitter", {}).get("submitted_at", "")),
            )

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT payload FROM reports WHERE report_id=?", (report_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_report_ids(self) -> List[str]:
        rows = self._conn.execute("SELECT report_id FROM reports").fetchall()
        return [r["report_id"] for r in rows]