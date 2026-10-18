"""Provisioned organisation/role credentials; no public self-enrolment."""
from dataclasses import dataclass, field
import hashlib
from typing import Optional
from fastapi import Header, HTTPException

ROLES = frozenset({'observer', 'fact_checker', 'official'})
_server_store = None


@dataclass(frozen=True)
class Identity:
    org: str
    role: str
    api_key: str = field(repr=False)

    @property
    def msp(self):
        return f'Org{self.org.removeprefix("org")}MSP'


def key_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def set_server_store(store):
    global _server_store
    _server_store = store


def require_org(x_api_key: Optional[str] = Header(None, alias='X-API-Key')):
    if not isinstance(x_api_key, str) or not x_api_key:
        raise HTTPException(401, 'missing X-API-Key')
    if _server_store is None:
        raise HTTPException(503, 'credential store unavailable')
    principal = _server_store.identity_for_key(x_api_key)
    if not principal:
        raise HTTPException(401, 'unknown or revoked credential')
    return Identity(principal['org'], principal['role'], x_api_key)


def permit(identity, *roles):
    if identity.role not in roles:
        raise HTTPException(403, 'role is not permitted for this operation')
