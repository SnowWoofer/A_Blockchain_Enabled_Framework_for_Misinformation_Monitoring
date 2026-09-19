from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, HTTPException, Header

# Configuration

AUTH_MODE = os.environ.get("AUTH_MODE", "bootstrap").strip().lower()
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_S = int(os.environ.get("JWT_EXPIRY_S", "86400"))  # 24 hours default


def get_auth_mode() -> str:
    """Read AUTH_MODE dynamically so env var changes at runtime are picked up."""
    return os.environ.get("AUTH_MODE", "bootstrap").strip().lower()

# Password hashing (SHA-256 + pepper, no bcrypt dependency for POC simplicity)

_PEPPER = os.environ.get("PASSWORD_PEPPER", "misinfo-monitor-2024")

def hash_password(password: str) -> str:
    #SHA-256(pepper + password)
    return hashlib.sha256(f"{_PEPPER}:{password}".encode("utf-8")).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(password), hashed)

# JWT helpers

def create_token(org: str) -> str:
    now = time.time()
    return jwt.encode(
        {"sub": org, "iat": now, "exp": now + JWT_EXPIRY_S},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def decode_token(token: str) -> Optional[str]:
    #Return the org name or None if invalid/expired
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except jwt.InvalidTokenError:
        return None

# User storage (JWT mode only)

class UserStore:
    #Thread-safe SQLite-backed user store for JWT auth mode.

    def __init__(self, db_path: str) -> None:
        parent = os.path.dirname(os.path.abspath(db_path))
        Path(parent).mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS org_users (
                    org        TEXT PRIMARY KEY,
                    password   TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def create_user(self, org: str, password: str) -> bool:
        #Create a new user. Returns False if org already exists.
        hashed = hash_password(password)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            with self._lock:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO org_users (org, password, created_at) VALUES (?, ?, ?)",(org, hashed, now),
                    )
            return True
        except sqlite3.IntegrityError:
            return False

    def verify_user(self, org: str, password: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT password FROM org_users WHERE org=?", (org,)
            ).fetchone()
        if not row:
            return False
        return verify_password(password, row["password"])

    def user_exists(self, org: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM org_users WHERE org=?", (org,)
            ).fetchone()
        return row is not None

# Dependency: require_org (dispatcher for bootstrap vs JWT mode)

_user_store: Optional[UserStore] = None
_server_store: Any = None


def init_user_store(db_path: str) -> None:
    global _user_store
    _user_store = UserStore(db_path)


def set_server_store(store: Any) -> None:
    global _server_store
    _server_store = store


def require_org_bootstrap(
    x_api_key: str = Header(..., alias="X-API-Key"),
    store: Any = None,
) -> str:
    #Bootstrap mode: authenticate via X-API-Key header
    from storage import STORE as _STORE
    s = store or _STORE
    if s is None:
        raise HTTPException(status_code=500, detail="store not initialized")
    org = s.org_for_key(x_api_key)
    if not org:
        raise HTTPException(status_code=401, detail="unknown API key")
    return org


def require_org_jwt(
    authorization: str = Header(..., alias="Authorization"),
) -> str:
    #JWT mode: authenticate via Bearer token
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    token = authorization[7:]
    org = decode_token(token)
    if not org:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return org


def require_org(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> str:
    # Dispatcher: checks AUTH_MODE and delegates to the right authenticator.
    mode = get_auth_mode()
    if mode == "jwt":
        if not authorization:
            raise HTTPException(status_code=401, detail="missing Authorization header")
        return require_org_jwt(authorization)
    # bootstrap mode (default)
    if not x_api_key:
        raise HTTPException(status_code=401, detail="missing X-API-Key header")
    if _server_store is None:
        raise HTTPException(status_code=500, detail="store not initialized")
    org = _server_store.org_for_key(x_api_key)
    if not org:
        raise HTTPException(status_code=401, detail="unknown API key")
    return org
