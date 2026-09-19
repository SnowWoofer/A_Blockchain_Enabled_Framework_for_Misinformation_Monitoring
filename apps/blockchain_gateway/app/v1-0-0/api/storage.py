from __future__ import annotations
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from report import canonical_json


class StorageError(Exception):
    pass


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
            headers={"Content-Type": "application/octet-stream"}, method="POST",
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

    def upsert_org_key(self, api_key: str, org: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO org_keys (api_key, org) VALUES (?, ?)",
                    (api_key, org),
                )

    def org_for_key(self, api_key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT org FROM org_keys WHERE api_key=?", (api_key,)
            ).fetchone()
        return row["org"] if row else None

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
