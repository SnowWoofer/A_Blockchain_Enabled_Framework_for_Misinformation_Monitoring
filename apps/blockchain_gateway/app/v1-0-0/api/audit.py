from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

GENESIS = "0" * 64


def _canonical(entry: Dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class AuditLog:
    FIELDS = ("ts", "actor", "org", "role", "action", "target", "outcome", "ip", "details")

    def __init__(self, db_path: str) -> None:
        Path(os.path.dirname(os.path.abspath(db_path))).mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        TEXT NOT NULL,
                    actor     TEXT NOT NULL,
                    org       TEXT NOT NULL,
                    role      TEXT NOT NULL,
                    action    TEXT NOT NULL,
                    target    TEXT NOT NULL,
                    outcome   TEXT NOT NULL,
                    ip        TEXT NOT NULL,
                    details   TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    hash      TEXT NOT NULL
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS audit_org ON audit_log(org, id)")

    @staticmethod
    def _digest(prev_hash: str, entry: Dict[str, Any]) -> str:
        return hashlib.sha256((prev_hash + _canonical(entry)).encode("utf-8")).hexdigest()

    def record(
        self, *, action: str, actor: str = "", org: str = "", role: str = "",
        target: str = "", outcome: str = "ok", ip: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "actor": actor, "org": org, "role": role, "action": action,
            "target": target, "outcome": outcome, "ip": ip,
            "details": _canonical(details or {}),
        }
        with self._lock:
            with self._conn:
                row = self._conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
                prev = row["hash"] if row else GENESIS
                self._conn.execute(
                    "INSERT INTO audit_log (ts, actor, org, role, action, target, outcome, ip, details, prev_hash, hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*[entry[f] for f in self.FIELDS], prev, self._digest(prev, entry)),
                )

    def entries(self, org: Optional[str] = None, limit: int = 100, before_id: Optional[int] = None,
                action: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses, params = [], []
        if org:
            clauses.append("org=?")
            params.append(org)
        if action:
            clauses.append("action=?")
            params.append(action)
        if before_id:
            clauses.append("id<?")
            params.append(before_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?", (*params, limit)
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["details"] = json.loads(item["details"] or "{}")
            out.append(item)
        return out

    def verify(self) -> Dict[str, Any]:
        prev = GENESIS
        count = 0
        with self._lock:
            rows = self._conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
        for r in rows:
            entry = {f: r[f] for f in self.FIELDS}
            if r["prev_hash"] != prev or self._digest(prev, entry) != r["hash"]:
                return {"intact": False, "entries": count, "first_bad_id": r["id"]}
            prev = r["hash"]
            count += 1
        return {"intact": True, "entries": count, "first_bad_id": None, "head": prev}


_log: Optional[AuditLog] = None


def init_audit(db_path: str) -> AuditLog:
    global _log
    _log = AuditLog(db_path)
    return _log


def get_audit() -> Optional[AuditLog]:
    return _log


def record(**kwargs: Any) -> None:
    # Best-effort: an audit write failure is logged, never turned into a failed request.
    if _log is None:
        return
    try:
        _log.record(**kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"[audit] failed to record {kwargs.get('action')}: {exc}")
