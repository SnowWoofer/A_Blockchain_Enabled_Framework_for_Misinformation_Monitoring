from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import bcrypt
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import audit
import totp
from security import SlidingWindowLimiter, client_ip

# Roles

ROLES = ("official", "fact_checker", "observer", "ai_submitter")
USER_ROLES = ("official", "fact_checker", "observer")
SIGNING_ROLE = {
    "official": "official",
    "fact_checker": "fact_checker",
    "observer": "fact_checker",  # queries only; the gateway never invokes as observer
    "ai_submitter": "fact_checker",
}
READ_ROLES = ("official", "fact_checker", "observer")
SUBMIT_ROLES = ("official", "fact_checker", "ai_submitter")
VOTE_ROLES = ("official", "fact_checker")
GOVERN_ROLES = ("official",)

# Configuration

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "misinfo-gateway"
JWT_AUDIENCE = "misinfo-api"
ACCESS_TTL_S = int(os.environ.get("ACCESS_TOKEN_TTL_S", "900"))
REFRESH_TTL_S = int(os.environ.get("REFRESH_TOKEN_TTL_S", str(8 * 3600)))
REFRESH_COOKIE = "mm_refresh"
# A just-rotated refresh token may be presented once more within this window (two tabs refreshing at the same moment) without being treated as theft.
REFRESH_REUSE_GRACE_S = float(os.environ.get("REFRESH_REUSE_GRACE_S", "20"))
MAX_FAILED_LOGINS = int(os.environ.get("MAX_FAILED_LOGINS", "5"))
LOCKOUT_S = int(os.environ.get("LOCKOUT_S", "900"))
BCRYPT_ROUNDS = int(os.environ.get("BCRYPT_ROUNDS", "12"))
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def get_auth_mode() -> str:
    #`jwt` (default): user accounts and API keys. `bootstrap`: API keys only, the original benchmark-only behaviour.
    return os.environ.get("AUTH_MODE", "jwt").strip().lower()


AUTH_MODE = get_auth_mode()


def cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "1") != "0"


_secrets: Dict[str, str] = {}


def _secret(name: str) -> str:
    if name not in _secrets:
        value = os.environ.get(name, "")
        if not value:
            value = secrets.token_hex(32)
            print(f"WARNING: {name} is not set; using a random per-process value, so it resets "
                  f"on restart. Run blockchain/scripts/gen-secrets.sh.")
        _secrets[name] = value
    return _secrets[name]


def jwt_secret() -> str:
    return _secret("JWT_SECRET")


def msp_for(org: str) -> str:
    return f"Org{org.removeprefix('org')}MSP"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# Passwords (bcrypt). Policy follows NIST SP 800-63B: length and a blocklist
# rather than composition rules.

_COMMON_PASSWORDS = {
    "password1234", "password12345", "123456789012", "qwertyuiop12", "letmein12345",
    "changeme1234", "administrator", "welcome12345", "iloveyou1234", "passwordpassword",
    "misinformation", "factchecker1", "factchecker123", "admin1234567", "qwerty123456",
}


def password_problems(password: str, username: str = "") -> List[str]:
    problems = []
    if len(password) < 12:
        problems.append("must be at least 12 characters")
    if len(password.encode("utf-8")) > 72:
        problems.append("must be at most 72 bytes")
    lowered = password.lower()
    if username and username.lower() in lowered:
        problems.append("must not contain the username")
    if lowered in _COMMON_PASSWORDS or len(set(password)) < 5:
        problems.append("is too common or repetitive")
    return problems


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:  # >72-byte input or a malformed hash
        return False


_TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_temp_password() -> str:
    return "-".join("".join(secrets.choice(_TEMP_ALPHABET) for _ in range(4)) for _ in range(4))


# Compared against when the username does not exist, so a failed login takes the same time whether or not the account is real.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))

# User store

_USER_FIELDS = ("org", "role", "display_name", "active", "must_change_password", "failed_attempts",
                "locked_until", "totp_secret", "totp_pending", "totp_last_step", "last_login_at")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class UserStore:
    def __init__(self, db_path: str) -> None:
        Path(os.path.dirname(os.path.abspath(db_path))).mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username             TEXT PRIMARY KEY,
                    org                  TEXT NOT NULL,
                    role                 TEXT NOT NULL,
                    display_name         TEXT NOT NULL DEFAULT '',
                    password_hash        TEXT NOT NULL,
                    active               INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    failed_attempts      INTEGER NOT NULL DEFAULT 0,
                    locked_until         REAL NOT NULL DEFAULT 0,
                    totp_secret          TEXT NOT NULL DEFAULT '',
                    totp_pending         TEXT NOT NULL DEFAULT '',
                    totp_last_step       INTEGER NOT NULL DEFAULT -1,
                    created_at           TEXT NOT NULL,
                    created_by           TEXT NOT NULL DEFAULT '',
                    last_login_at        TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    sid        TEXT PRIMARY KEY,
                    username   TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked    INTEGER NOT NULL DEFAULT 0,
                    ip         TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    token_hash TEXT PRIMARY KEY,
                    sid        TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    used       INTEGER NOT NULL DEFAULT 0,
                    used_at    REAL NOT NULL DEFAULT 0
                )
                """
            )
            # The previous JWT mode had one shared password per org. Carry those accounts over as `orgN` officials who must pick a new password at their next sign-in.
            legacy = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='org_users'"
            ).fetchone()
            if legacy:
                for row in self._conn.execute("SELECT org, password, created_at FROM org_users").fetchall():
                    self._conn.execute(
                        "INSERT OR IGNORE INTO users (username, org, role, password_hash, "
                        "must_change_password, created_at, created_by) VALUES (?, ?, 'official', ?, 1, ?, 'migrated')",
                        (row["org"], row["org"], row["password"], row["created_at"]),
                    )
                self._conn.execute("DROP TABLE org_users")

    def create_user(self, username: str, org: str, role: str, password: str, *,
                    display_name: str = "", created_by: str = "", must_change: bool = True) -> bool:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO users (username, org, role, display_name, password_hash, must_change_password, "
                    "created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (username, org, role, display_name, hash_password(password), int(must_change),
                     _now_iso(), created_by),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def list_users(self, org: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if org:
                rows = self._conn.execute("SELECT * FROM users WHERE org=? ORDER BY username", (org,)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM users ORDER BY org, username").fetchall()
        return [dict(r) for r in rows]

    def update_user(self, username: str, **fields: Any) -> None:
        unknown = set(fields) - set(_USER_FIELDS)
        if unknown:
            raise ValueError(f"unknown user fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{k}=?" for k in fields)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE users SET {assignments} WHERE username=?", (*fields.values(), username))

    def set_password(self, username: str, password: str, *, must_change: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET password_hash=?, must_change_password=?, failed_attempts=0, locked_until=0 "
                "WHERE username=?",
                (hash_password(password), int(must_change), username),
            )

    def register_failure(self, username: str) -> Tuple[int, float]:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT failed_attempts FROM users WHERE username=?", (username,)).fetchone()
            failed = (row["failed_attempts"] if row else 0) + 1
            locked_until = time.time() + LOCKOUT_S if failed >= MAX_FAILED_LOGINS else 0.0
            self._conn.execute(
                "UPDATE users SET failed_attempts=?, locked_until=? WHERE username=?",
                (0 if locked_until else failed, locked_until, username),
            )
        return failed, locked_until

    def clear_failures(self, username: str) -> None:
        self.update_user(username, failed_attempts=0, locked_until=0)

    # sessions

    def create_session(self, username: str, ip: str = "", user_agent: str = "") -> Tuple[str, str]:
        sid = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions (sid, username, created_at, expires_at, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, username, now, now + REFRESH_TTL_S, ip, user_agent),
            )
            self._conn.execute(
                "INSERT INTO refresh_tokens (token_hash, sid, expires_at) VALUES (?, ?, ?)",
                (_hash_token(token), sid, now + REFRESH_TTL_S),
            )
        return sid, token

    def rotate_refresh(self, token: str) -> Tuple[str, str, str, str]:
        # (status, sid, username, new_token). status is ok | invalid | expired | reused. A token presented again after rotation (outside the short grace window) revokes its whole session, because only a stolen copy should still be in circulation.
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT r.sid, r.used, r.used_at, r.expires_at, s.username, s.revoked, s.expires_at AS s_exp "
                "FROM refresh_tokens r JOIN sessions s ON s.sid = r.sid WHERE r.token_hash=?",
                (_hash_token(token),),
            ).fetchone()
            if row is None:
                return "invalid", "", "", ""
            if row["used"] and now - row["used_at"] > REFRESH_REUSE_GRACE_S:
                self._conn.execute("UPDATE sessions SET revoked=1 WHERE sid=?", (row["sid"],))
                return "reused", row["sid"], row["username"], ""
            if row["revoked"] or row["expires_at"] < now or row["s_exp"] < now:
                return "expired", row["sid"], row["username"], ""
            new_token = secrets.token_urlsafe(32)
            if not row["used"]:
                self._conn.execute("UPDATE refresh_tokens SET used=1, used_at=? WHERE token_hash=?",
                                   (now, _hash_token(token)))
            self._conn.execute(
                "INSERT INTO refresh_tokens (token_hash, sid, expires_at) VALUES (?, ?, ?)",
                (_hash_token(new_token), row["sid"], row["s_exp"]),
            )
        return "ok", row["sid"], row["username"], new_token

    def sid_for_refresh(self, token: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT sid FROM refresh_tokens WHERE token_hash=?", (_hash_token(token),)
            ).fetchone()
        return row["sid"] if row else None

    def session_active(self, sid: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT revoked, expires_at FROM sessions WHERE sid=?", (sid,)
            ).fetchone()
        return bool(row) and not row["revoked"] and row["expires_at"] > time.time()

    def revoke_session(self, sid: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE sessions SET revoked=1 WHERE sid=?", (sid,))

    def revoke_user_sessions(self, username: str, except_sid: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET revoked=1 WHERE username=? AND sid<>?", (username, except_sid)
            )


_user_store: Optional[UserStore] = None
_server_store: Any = None


def init_user_store(db_path: str) -> UserStore:
    global _user_store
    _user_store = UserStore(db_path)
    return _user_store


def get_user_store() -> UserStore:
    if _user_store is None:
        raise HTTPException(status_code=500, detail="user store not initialized")
    return _user_store


def set_server_store(store: Any) -> None:
    global _server_store
    _server_store = store

# Principals

@dataclass(frozen=True)
class Principal:
    subject: str  # username, or key:<label> for API keys
    org: str
    role: str
    kind: str  # "user" | "api_key"
    sid: str = ""
    must_change_password: bool = False

    @property
    def fabric_role(self) -> str:
        return SIGNING_ROLE.get(self.role, "fact_checker")

    @property
    def mspid(self) -> str:
        return msp_for(self.org)


def create_access_token(user: Dict[str, Any], sid: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "sub": user["username"], "org": user["org"],
            "role": user["role"], "sid": sid, "jti": secrets.token_hex(8), "iat": now,
            "exp": now + ACCESS_TTL_S,
        },
        jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(token: str, verify_exp: bool = True) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(
            token, jwt_secret(), algorithms=[JWT_ALGORITHM], audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "sid"], "verify_exp": verify_exp},
        )
    except jwt.InvalidTokenError:
        return None


def _principal_from_api_key(api_key: str) -> Principal:
    if _server_store is None:
        raise HTTPException(status_code=500, detail="store not initialized")
    found = _server_store.principal_for_key(api_key)
    if not found:
        raise HTTPException(status_code=401, detail="unknown API key")
    org, role, label = found
    return Principal(subject=f"key:{label or org}", org=org,
                     role=role if role in ROLES else "observer", kind="api_key")


def _principal_from_jwt(token: str) -> Principal:
    claims = decode_access_token(token)
    if claims is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    store = get_user_store()
    user = store.get_user(claims["sub"])
    # A role change or org move invalidates outstanding tokens immediately.
    if (not user or not user["active"] or user["org"] != claims.get("org")
            or user["role"] != claims.get("role")):
        raise HTTPException(status_code=401, detail="invalid or expired token")
    if not store.session_active(claims["sid"]):
        raise HTTPException(status_code=401, detail="session has ended; sign in again")
    return Principal(subject=user["username"], org=user["org"], role=user["role"], kind="user",
                     sid=claims["sid"], must_change_password=bool(user["must_change_password"]))


def get_principal(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> Principal:
    if x_api_key:
        principal = _principal_from_api_key(x_api_key)
    elif get_auth_mode() == "jwt":
        if not authorization:
            raise HTTPException(status_code=401, detail="missing credentials (Authorization: Bearer or X-API-Key)")
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing Bearer token")
        principal = _principal_from_jwt(authorization[7:])
    else:
        raise HTTPException(status_code=401, detail="missing X-API-Key header")
    request.state.principal = principal
    return principal


def _deny(request: Request, principal: Principal, detail: str) -> HTTPException:
    audit.record(action="access_denied", actor=principal.subject, org=principal.org, role=principal.role,
                 target=f"{request.method} {request.url.path}", outcome="denied", ip=client_ip(request))
    return HTTPException(status_code=403, detail=detail)


def require(*roles: str, humans_only: bool = False):
    # Dependency: the caller must hold one of `roles` (and, with humans_only, be a signed-in user rather than an API key).
    def dependency(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
        if principal.must_change_password:
            raise HTTPException(status_code=403, detail="password change required before continuing")
        if principal.role not in roles:
            raise _deny(request, principal, f"role '{principal.role}' may not perform this action")
        if humans_only and principal.kind != "user":
            raise _deny(request, principal, "this action requires a signed-in user account")
        return principal
    return dependency


def reviewer_ref(principal: Principal) -> str:
    # Stable pseudonym for the individual behind a vote. Published in the off-chain document so an org can trace its own reviewers (it can recompute the HMAC) while outsiders cannot single out and target a fact-checker.
    key = _secret("PSEUDONYM_KEY").encode("utf-8")
    return hmac.new(key, f"{principal.org}:{principal.subject}".encode("utf-8"), hashlib.sha256).hexdigest()[:16]

# Routes: /api/auth/* (sessions) and /api/users/* (org user administration)

router = APIRouter()

_login_ip_limiter = SlidingWindowLimiter(int(os.environ.get("LOGIN_IP_LIMIT", "30")), 300)
_login_user_limiter = SlidingWindowLimiter(10, 300)


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)
    otp: Optional[str] = Field(None, max_length=12)


class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)


class MfaCode(BaseModel):
    code: str = Field(..., min_length=6, max_length=12)


class MfaDisable(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)
    code: str = Field(..., min_length=6, max_length=12)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    role: Literal["official", "fact_checker", "observer"]
    display_name: str = Field("", max_length=100)


class UserUpdate(BaseModel):
    active: Optional[bool] = None
    role: Optional[Literal["official", "fact_checker", "observer"]] = None
    display_name: Optional[str] = Field(None, max_length=100)


def _require_jwt_mode() -> None:
    if get_auth_mode() != "jwt":
        raise HTTPException(status_code=400, detail="user accounts are only available in JWT auth mode (AUTH_MODE=jwt)")


def _require_xhr(request: Request) -> None:
    # Cookie-authenticated endpoints also demand a custom header: a cross-site form post cannot set one, so this closes CSRF even if the SameSite cookie attribute is ignored by an old browser.
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        raise HTTPException(status_code=403, detail="missing X-Requested-With header")


def user_view(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "username": user["username"], "org": user["org"], "mspid": msp_for(user["org"]),
        "role": user["role"], "display_name": user["display_name"], "active": bool(user["active"]),
        "must_change_password": bool(user["must_change_password"]),
        "mfa_enabled": bool(user["totp_secret"]), "locked": user["locked_until"] > time.time(),
        "created_at": user["created_at"], "created_by": user["created_by"],
        "last_login_at": user["last_login_at"],
    }


def _session_response(response: Response, user: Dict[str, Any], sid: str, refresh_token: str) -> Dict[str, Any]:
    response.set_cookie(
        REFRESH_COOKIE, refresh_token, max_age=REFRESH_TTL_S, httponly=True,
        secure=cookie_secure(), samesite="strict", path="/api/auth",
    )
    return {
        "access_token": create_access_token(user, sid), "token_type": "bearer",
        "expires_in": ACCESS_TTL_S, "user": user_view(user),
    }


def _ended(detail: str) -> JSONResponse:
    resp = JSONResponse({"detail": detail}, status_code=401)
    resp.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    return resp


def _ctx(principal: Principal, request: Request) -> Dict[str, str]:
    return {"actor": principal.subject, "org": principal.org, "role": principal.role, "ip": client_ip(request)}


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    _require_jwt_mode()
    ip = client_ip(request)
    username = body.username.strip().lower()
    if not _login_ip_limiter.allow(ip) or not _login_user_limiter.allow(username):
        audit.record(action="login_rate_limited", actor=username, ip=ip, outcome="denied")
        raise HTTPException(status_code=429, detail="too many sign-in attempts; try again in a few minutes")
    store = get_user_store()
    user = store.get_user(username)
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        audit.record(action="login_failed", actor=username, ip=ip, outcome="denied", details={"reason": "unknown_user"})
        raise HTTPException(status_code=401, detail="invalid credentials")
    ctx = {"actor": username, "org": user["org"], "role": user["role"], "ip": ip}
    if user["locked_until"] > time.time():
        audit.record(action="login_locked", outcome="denied", **ctx)
        raise HTTPException(status_code=423, detail="account temporarily locked after repeated failed sign-ins")
    if not verify_password(body.password, user["password_hash"]):
        failed, locked_until = store.register_failure(username)
        audit.record(action="login_failed", outcome="denied",
                     details={"reason": "bad_password", "failed_attempts": failed}, **ctx)
        if locked_until:
            audit.record(action="account_locked", outcome="denied", **ctx)
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not user["active"]:
        audit.record(action="login_failed", outcome="denied", details={"reason": "disabled"}, **ctx)
        raise HTTPException(status_code=401, detail="invalid credentials")
    if user["totp_secret"]:
        if not body.otp:
            raise HTTPException(status_code=401, detail={
                "code": "mfa_required", "message": "Enter the 6-digit code from your authenticator app."})
        step = totp.verify(user["totp_secret"], body.otp, user["totp_last_step"])
        if step is None:
            store.register_failure(username)
            audit.record(action="login_failed", outcome="denied", details={"reason": "bad_otp"}, **ctx)
            raise HTTPException(status_code=401, detail={
                "code": "mfa_invalid", "message": "That code is invalid or has already been used."})
        store.update_user(username, totp_last_step=step)
    store.clear_failures(username)
    store.update_user(username, last_login_at=_now_iso())
    _login_user_limiter.reset(username)
    sid, refresh_token = store.create_session(username, ip, request.headers.get("user-agent", "")[:200])
    audit.record(action="login", details={"session": sid[:8]}, **ctx)
    return _session_response(response, store.get_user(username), sid, refresh_token)


@router.post("/api/auth/refresh")
def refresh(request: Request, response: Response):
    _require_jwt_mode()
    _require_xhr(request)
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        return _ended("no active session")
    store = get_user_store()
    status, sid, username, new_token = store.rotate_refresh(token)
    if status == "reused":
        audit.record(action="refresh_token_reuse", actor=username, ip=client_ip(request), outcome="denied",
                     details={"session": sid[:8], "effect": "session revoked"})
        return _ended("session ended for your protection; sign in again")
    if status != "ok":
        return _ended("session has ended; sign in again")
    user = store.get_user(username)
    if not user or not user["active"]:
        store.revoke_session(sid)
        return _ended("session has ended; sign in again")
    return _session_response(response, user, sid, new_token)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response,
           authorization: Optional[str] = Header(None, alias="Authorization")):
    _require_jwt_mode()
    _require_xhr(request)
    store = get_user_store()
    sid = None
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        sid = store.sid_for_refresh(token)
    if not sid and authorization and authorization.startswith("Bearer "):
        claims = decode_access_token(authorization[7:], verify_exp=False)
        sid = claims.get("sid") if claims else None
    if sid:
        store.revoke_session(sid)
        audit.record(action="logout", ip=client_ip(request), details={"session": sid[:8]})
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    return {"ok": True}


@router.get("/api/auth/me")
def me(principal: Principal = Depends(get_principal)):
    out = {
        "subject": principal.subject, "org": principal.org, "mspid": principal.mspid,
        "role": principal.role, "kind": principal.kind,
        "must_change_password": principal.must_change_password,
    }
    if principal.kind == "user":
        out["user"] = user_view(get_user_store().get_user(principal.subject))
    return out


def _require_user(principal: Principal) -> Dict[str, Any]:
    if principal.kind != "user":
        raise HTTPException(status_code=400, detail="API keys have no password or second factor")
    user = get_user_store().get_user(principal.subject)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return user


@router.post("/api/auth/password")
def change_password(body: PasswordChange, request: Request, principal: Principal = Depends(get_principal)):
    user = _require_user(principal)
    store = get_user_store()
    if not verify_password(body.current_password, user["password_hash"]):
        audit.record(action="password_change_failed", outcome="denied", **_ctx(principal, request))
        raise HTTPException(status_code=400, detail="current password is incorrect")
    problems = password_problems(body.new_password, principal.subject)
    if body.new_password == body.current_password:
        problems.append("must differ from the current password")
    if problems:
        raise HTTPException(status_code=400, detail="New password " + "; ".join(problems) + ".")
    store.set_password(principal.subject, body.new_password, must_change=False)
    store.revoke_user_sessions(principal.subject, except_sid=principal.sid)
    audit.record(action="password_changed", **_ctx(principal, request))
    return {"ok": True}


@router.post("/api/auth/mfa/setup")
def mfa_setup(request: Request, principal: Principal = Depends(get_principal)):
    user = _require_user(principal)
    if user["totp_secret"]:
        raise HTTPException(status_code=400, detail="two-factor authentication is already enabled")
    secret = totp.new_secret()
    get_user_store().update_user(principal.subject, totp_pending=secret)
    return {"secret": secret, "otpauth_uri": totp.provisioning_uri(secret, f"{principal.subject}@{principal.org}")}


@router.post("/api/auth/mfa/enable")
def mfa_enable(body: MfaCode, request: Request, principal: Principal = Depends(get_principal)):
    user = _require_user(principal)
    if not user["totp_pending"]:
        raise HTTPException(status_code=400, detail="start two-factor setup first")
    step = totp.verify(user["totp_pending"], body.code)
    if step is None:
        raise HTTPException(status_code=400, detail="that code did not match; check your device clock and try again")
    store = get_user_store()
    store.update_user(principal.subject, totp_secret=user["totp_pending"], totp_pending="", totp_last_step=step)
    store.revoke_user_sessions(principal.subject, except_sid=principal.sid)
    audit.record(action="mfa_enabled", **_ctx(principal, request))
    return {"ok": True}


@router.post("/api/auth/mfa/disable")
def mfa_disable(body: MfaDisable, request: Request, principal: Principal = Depends(get_principal)):
    user = _require_user(principal)
    if not user["totp_secret"]:
        raise HTTPException(status_code=400, detail="two-factor authentication is not enabled")
    if (not verify_password(body.password, user["password_hash"])
            or totp.verify(user["totp_secret"], body.code, user["totp_last_step"]) is None):
        audit.record(action="mfa_disable_failed", outcome="denied", **_ctx(principal, request))
        raise HTTPException(status_code=400, detail="password or code is incorrect")
    get_user_store().update_user(principal.subject, totp_secret="", totp_pending="", totp_last_step=-1)
    audit.record(action="mfa_disabled", **_ctx(principal, request))
    return {"ok": True}


_org_admin = require("official", humans_only=True)


def _member(username: str, principal: Principal) -> Dict[str, Any]:
    user = get_user_store().get_user(username)
    # Users of other orgs are reported as missing, not forbidden.
    if user is None or user["org"] != principal.org:
        raise HTTPException(status_code=404, detail="no such user in your organisation")
    return user


@router.get("/api/users")
def list_users(principal: Principal = Depends(_org_admin)):
    return [user_view(u) for u in get_user_store().list_users(principal.org)]


@router.post("/api/users")
def create_user(body: UserCreate, request: Request, principal: Principal = Depends(_org_admin)):
    _require_jwt_mode()
    username = body.username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(status_code=400, detail="username: 3-64 chars of a-z, 0-9, '.', '_' or '-'")
    temp = generate_temp_password()
    created = get_user_store().create_user(
        username, principal.org, body.role, temp, display_name=body.display_name.strip(),
        created_by=principal.subject, must_change=True,
    )
    if not created:
        raise HTTPException(status_code=409, detail="that username is taken")
    audit.record(action="user_created", target=username, details={"role": body.role}, **_ctx(principal, request))
    return {"user": user_view(get_user_store().get_user(username)), "temporary_password": temp}


@router.patch("/api/users/{username}")
def update_user(username: str, body: UserUpdate, request: Request, principal: Principal = Depends(_org_admin)):
    user = _member(username, principal)
    if username == principal.subject and (body.active is False or (body.role and body.role != user["role"])):
        raise HTTPException(status_code=400, detail="you cannot disable or change the role of your own account")
    changes: Dict[str, Any] = {}
    if body.active is not None:
        changes["active"] = int(body.active)
    if body.role is not None:
        changes["role"] = body.role
    if body.display_name is not None:
        changes["display_name"] = body.display_name.strip()
    store = get_user_store()
    store.update_user(username, **changes)
    if body.active is False or (body.role and body.role != user["role"]):
        store.revoke_user_sessions(username)
    audit.record(action="user_updated", target=username,
                 details={k: v for k, v in changes.items() if k != "display_name"}, **_ctx(principal, request))
    return user_view(store.get_user(username))


@router.post("/api/users/{username}/reset")
def reset_user(username: str, request: Request, principal: Principal = Depends(_org_admin)):
    # Account recovery: new one-time password, second factor cleared, all sessions ended. The user must choose a new password at next sign-in.
    _member(username, principal)
    temp = generate_temp_password()
    store = get_user_store()
    store.set_password(username, temp, must_change=True)
    store.update_user(username, totp_secret="", totp_pending="", totp_last_step=-1)
    store.revoke_user_sessions(username)
    audit.record(action="user_reset", target=username, **_ctx(principal, request))
    return {"user": user_view(store.get_user(username)), "temporary_password": temp}
