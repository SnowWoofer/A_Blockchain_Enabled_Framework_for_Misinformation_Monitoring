from __future__ import annotations
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from report import canonical_json


class StorageError(Exception):
    pass


def hash_api_key(api_key: str) -> str:
    # API keys are high-entropy bearer secrets, so a plain digest is enough to
    # keep them out of the database without slowing every request with bcrypt.
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def internal_headers() -> Dict[str, str]:
    """Shared-secret header for the gateway's own sidecars (fabric_gateway,
    ipfs_gateway). Empty when INTERNAL_API_TOKEN is unset (dev only)."""
    token = os.environ.get("INTERNAL_API_TOKEN", "")
    return {"X-Internal-Token": token} if token else {}


_FTS_TOKEN = re.compile(r"\w+", re.UNICODE)


class CryptoGate:
    STYLES = ("ecdsa", "ed25519", "rsa")

    def __init__(self, style: Optional[str] = None) -> None:
        self.style = (style or os.environ.get("CRYPTO_STYLE", "ecdsa")).lower()
        if self.style not in self.STYLES:
            raise StorageError(
                f"CRYPTO_STYLE must be one of {self.STYLES}, got '{self.style}'"
            )
        self._keypair = self._generate()
        self._last_ms: float = 0.0

    def _generate(self) -> Any:
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
        if self.style == "ecdsa":
            private = ec.generate_private_key(ec.SECP256R1())
        elif self.style == "ed25519":
            private = ed25519.Ed25519PrivateKey.generate()
        else:
            private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return private

    def verify_token(self, token: bytes) -> bool:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding
        start = time.perf_counter()
        try:
            if self.style == "rsa":
                ciphertext = self._keypair.public_key().encrypt(
                    token, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
                )
                self._keypair.decrypt(
                    ciphertext, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
                )
            elif self.style == "ed25519":
                signature = self._keypair.sign(token)
                self._keypair.public_key().verify(signature, token)
            else:
                signature = self._keypair.sign(token, ec.ECDSA(hashes.SHA256()))
                self._keypair.public_key().verify(signature, token, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            return False
        finally:
            self._last_ms = (time.perf_counter() - start) * 1000.0
        return True

    def status(self) -> Dict[str, Any]:
        return {"crypto_style": self.style, "crypto_verify_ms": round(self._last_ms, 3)}


class IpfsStore:
    """Thin client for apps/ipfs_gateway — a generic IPFS read/write bridge,
    independently reachable by other consumers too (not just this service).
    This class no longer talks to kubo directly; it just forwards bytes to
    that bridge, same relationship as FabricGatewayBridge has with
    apps/fabric_gateway."""

    def __init__(self, api_url: Optional[str] = None) -> None:
        self.api_url = (api_url or os.environ.get("IPFS_GATEWAY_URL", "http://localhost:9101")).rstrip("/")

    def is_available(self) -> bool:
        req = urllib.request.Request(f"{self.api_url}/health", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                resp = json.loads(r.read().decode("utf-8"))
            return bool(resp.get("ipfs_available"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return False

    def add_bytes(self, data: bytes) -> str:
        req = urllib.request.Request(
            f"{self.api_url}/add", data=data,
            headers={"Content-Type": "application/octet-stream", **internal_headers()},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"ipfs_gateway add failed: {exc}") from exc
        cid = resp.get("cid")
        if not cid:
            raise StorageError(f"ipfs_gateway add returned no CID: {resp}")
        return cid

    def cat_bytes(self, cid: str, timeout: int = 15) -> bytes:
        req = urllib.request.Request(f"{self.api_url}/cat/{cid}", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, OSError) as exc:
            raise StorageError(f"ipfs_gateway cat failed: {exc}") from exc


class OffChainStore:

    def __init__(
        self,
        db_path: str = "offchain.db",
        ipfs: Optional[Union[str, IpfsStore]] = None,
        crypto_style: Optional[str] = None,
    ) -> None:
        parent = os.path.dirname(os.path.abspath(db_path))
        Path(parent).mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()
        if ipfs is None:
            ipfs = IpfsStore()
        self._ipfs = ipfs if isinstance(ipfs, IpfsStore) else IpfsStore(ipfs)
        self._ipfs_ok = self._ipfs.is_available()
        self._gate = CryptoGate(crypto_style)

    def _init(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_hash   TEXT PRIMARY KEY,
                    org        TEXT NOT NULL,
                    role       TEXT NOT NULL DEFAULT 'official',
                    label      TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    revoked    INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # Older databases kept API keys in plaintext (org_keys). Hash them
            # into api_keys once and drop the plaintext copy.
            legacy = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='org_keys'"
            ).fetchone()
            if legacy:
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                for row in self._conn.execute("SELECT api_key, org FROM org_keys").fetchall():
                    self._conn.execute(
                        "INSERT OR IGNORE INTO api_keys (key_hash, org, role, label, created_at) "
                        "VALUES (?, ?, 'official', 'migrated', ?)",
                        (hash_api_key(row["api_key"]), row["org"], now),
                    )
                self._conn.execute("DROP TABLE org_keys")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS report_meta (
                    report_id  TEXT PRIMARY KEY,
                    msg_id     TEXT NOT NULL DEFAULT '',
                    platform   TEXT NOT NULL DEFAULT '',
                    content    TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS report_meta_msg ON report_meta(msg_id)")
            try:
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS report_fts USING "
                    "fts5(content, report_id UNINDEXED, tokenize='unicode61 remove_diacritics 2')"
                )
                self._fts = True
            except sqlite3.OperationalError:
                self._fts = False  # sqlite built without FTS5: fall back to LIKE
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
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS report_index (
                    report_id    TEXT PRIMARY KEY,
                    cid          TEXT NOT NULL,
                    content_hash TEXT NOT NULL DEFAULT '',
                    created_at   TEXT NOT NULL
                )
                """
            )
            cols = [r[1] for r in self._conn.execute("PRAGMA table_info(report_index)").fetchall()]
            if "content_hash" not in cols:
                self._conn.execute(
                    "ALTER TABLE report_index ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"
                )
    def _ipfs_ready(self) -> bool:
        """`_ipfs_ok` is a cache, not a permanent verdict: if IPFS was down
        (or ipfs_gateway just hadn't finished starting yet — a real race on
        every fresh `docker compose up`, not just a rare edge case), retry
        here rather than staying stuck on SQLite for the rest of the
        process's life once the dependency comes back."""
        if not self._ipfs_ok:
            self._ipfs_ok = self._ipfs.is_available()
        return self._ipfs_ok

    @property
    def using_ipfs(self) -> bool:
        return self._ipfs_ready()

    def ipfs_status(self) -> Dict[str, Any]:
        ready = self._ipfs_ready()
        status = {
            "ipfs_available": ready,
            "backend": "ipfs" if ready else "sqlite",
        }
        status.update(self._gate.status())
        return status

    def verify_onboarding_token(self, org: str, token: bytes) -> bool:
        return self._gate.verify_token(token)

    def upsert_org_key(self, api_key: str, org: str, role: str = "official", label: str = "") -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO api_keys (key_hash, org, role, label, created_at, revoked) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (hash_api_key(api_key), org, role, label, now),
                )

    def principal_for_key(self, api_key: str) -> Optional[Tuple[str, str, str]]:
        """(org, role, label) for a live API key, else None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT org, role, label FROM api_keys WHERE key_hash=? AND revoked=0",
                (hash_api_key(api_key),),
            ).fetchone()
        return (row["org"], row["role"], row["label"]) if row else None

    def org_for_key(self, api_key: str) -> Optional[str]:
        found = self.principal_for_key(api_key)
        return found[0] if found else None

    def revoke_keys(self, *, label: Optional[str] = None, api_key: Optional[str] = None) -> int:
        with self._lock:
            with self._conn:
                if api_key is not None:
                    cur = self._conn.execute(
                        "UPDATE api_keys SET revoked=1 WHERE key_hash=?", (hash_api_key(api_key),)
                    )
                else:
                    cur = self._conn.execute("UPDATE api_keys SET revoked=1 WHERE label=?", (label,))
        return cur.rowcount

    # --- local search index (public "has this claim been checked?" lookup) --

    def index_report(self, report_id: str, msg_id: str, platform: str, content: str, created_at: str) -> None:
        with self._lock:
            with self._conn:
                existed = self._conn.execute(
                    "SELECT 1 FROM report_meta WHERE report_id=?", (report_id,)
                ).fetchone()
                self._conn.execute(
                    "INSERT OR REPLACE INTO report_meta (report_id, msg_id, platform, content, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (report_id, msg_id, platform, content, created_at),
                )
                if self._fts and not existed:
                    self._conn.execute(
                        "INSERT INTO report_fts (content, report_id) VALUES (?, ?)", (content, report_id)
                    )

    def report_meta(self, report_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not report_ids:
            return {}
        marks = ",".join("?" * len(report_ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT report_id, msg_id, platform, content, created_at FROM report_meta "
                f"WHERE report_id IN ({marks})",
                list(report_ids),
            ).fetchall()
        return {r["report_id"]: dict(r) for r in rows}

    def report_id_for_msg(self, msg_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT report_id FROM report_meta WHERE msg_id=? ORDER BY created_at DESC LIMIT 1",
                (msg_id,),
            ).fetchone()
        return row["report_id"] if row else None

    def search_reports(self, query: str, limit: int = 500) -> List[str]:
        """Report ids whose claim text matches every word in `query`. Words are
        extracted and quoted, so user input can never form FTS5 syntax."""
        tokens = _FTS_TOKEN.findall(query)[:12]
        if not tokens:
            return []
        with self._lock:
            if self._fts:
                expr = " ".join('"' + t.replace('"', "") + '"' for t in tokens)
                rows = self._conn.execute(
                    "SELECT report_id FROM report_fts WHERE report_fts MATCH ? ORDER BY rank LIMIT ?",
                    (expr, limit),
                ).fetchall()
            else:
                clause = " AND ".join("content LIKE ?" for _ in tokens)
                rows = self._conn.execute(
                    f"SELECT report_id FROM report_meta WHERE {clause} LIMIT ?",
                    [f"%{t}%" for t in tokens] + [limit],
                ).fetchall()
        return [r["report_id"] for r in rows]

    def save_report(self, report: Dict[str, Any]) -> str:
        # content_hash is set by report.make_report() over the claim's fixed
        # core fields (source/inference) and must not be touched here
        # — it has to stay identical across every later version of this
        # claim's document (see report._core_content).
        created_at = report.get("submitter", {}).get("submitted_at", "")
        content = report["content_hash"]
        if self._ipfs_ready():
            try:
                blob = {k: v for k, v in report.items() if k != "report_id"}
                cid = self._ipfs.add_bytes(canonical_json(blob).encode("utf-8"))
                report["report_id"] = cid
                with self._lock:
                    with self._conn:
                        self._conn.execute(
                            "INSERT OR REPLACE INTO report_index (report_id, cid, content_hash, created_at) "
                            "VALUES (?, ?, ?, ?)",
                            (cid, cid, content, created_at),
                        )
                return f"ipfs://{cid}"
            except StorageError:
                self._ipfs_ok = False
                print(f"[storage] IPFS unreachable, falling back to SQLite for {report}")
        report["report_id"] = report.get("report_id") or uuid.uuid4().hex
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO reports (report_id, payload, content_hash, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (report["report_id"], canonical_json(report), content, created_at),
                )
        return f"http://localhost:8000/api/reports/{report['report_id']}"

    def save_report_version(self, report_id: str, report: Dict[str, Any]) -> str:
        """Publishes `report` (the current full snapshot — claim content plus
        every fact_check so far) as a new, immutable off-chain object without
        changing the claim's stable report_id/on-chain ledger key. The old
        CID stays resolvable in IPFS; this claim's version history is
        recoverable from the ledger's own transaction history
        (GET /api/reports/{id}/history) — nothing extra is tracked here."""
        report = {**report, "report_id": report_id}
        content = report["content_hash"]
        created_at = report.get("submitter", {}).get("submitted_at", "")
        if self._ipfs_ready():
            try:
                blob = {k: v for k, v in report.items() if k != "report_id"}
                cid = self._ipfs.add_bytes(canonical_json(blob).encode("utf-8"))
                with self._lock:
                    with self._conn:
                        self._conn.execute(
                            "INSERT OR REPLACE INTO report_index (report_id, cid, content_hash, created_at) "
                            "VALUES (?, ?, ?, ?)",
                            (report_id, cid, content, created_at),
                        )
                return f"ipfs://{cid}"
            except StorageError:
                self._ipfs_ok = False
                print(f"[storage] IPFS unreachable, falling back to SQLite for {report_id}")
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO reports (report_id, payload, content_hash, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (report_id, canonical_json(report), content, created_at),
                )
        return f"http://localhost:8000/api/reports/{report_id}"

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT cid, content_hash FROM report_index WHERE report_id=?", (report_id,)
            ).fetchone()
        if row:
            try:
                blob = json.loads(self._ipfs.cat_bytes(row["cid"]).decode("utf-8"))
                blob["report_id"] = report_id
                blob["content_hash"] = row["content_hash"]
                return blob
            except StorageError:
                pass
        with self._lock:
            local = self._conn.execute(
                "SELECT payload FROM reports WHERE report_id=?", (report_id,)
            ).fetchone()
        return json.loads(local["payload"]) if local else None

    def list_report_ids(self) -> List[str]:
        with self._lock:
            rows = self._conn.execute("SELECT report_id FROM reports").fetchall()
            ipfs_rows = self._conn.execute("SELECT report_id FROM report_index").fetchall()
        ids = {r["report_id"] for r in rows} | {r["report_id"] for r in ipfs_rows}
        return sorted(ids)
