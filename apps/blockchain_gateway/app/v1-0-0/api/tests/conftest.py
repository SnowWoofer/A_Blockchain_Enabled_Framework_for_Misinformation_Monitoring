from __future__ import annotations
import hashlib
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

# Must be set before the gateway modules are imported.
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ["COOKIE_SECURE"] = "0"  # the test client talks plain http
# Orgs the gateway can sign for, the ledger fixture registers only org1..org3.
os.environ["ORGS"] = ",".join(f"org{i}" for i in range(1, 10))
os.environ["JWT_SECRET"] = "test-jwt-secret-" + "x" * 32
os.environ["PSEUDONYM_KEY"] = "test-pseudonym-key"
os.environ["IPFS_GATEWAY_URL"] = "http://127.0.0.1:1"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from storage import IpfsStore, OffChainStore  # noqa: E402

PASSWORD = "correct-horse-battery"


class FakeIpfs(IpfsStore):
    #Content-addressed in-memory stand-in for apps/ipfs_gateway.

    def __init__(self) -> None:
        super().__init__("http://fake-ipfs")
        self.blobs: Dict[str, bytes] = {}

    def is_available(self) -> bool:
        return True

    def add_bytes(self, data: bytes) -> str:
        cid = "Qm" + hashlib.sha256(data).hexdigest()[:44]
        self.blobs[cid] = data
        return cid

    def cat_bytes(self, cid: str, timeout: int = 15) -> bytes:
        return self.blobs[cid]


class RecordingBridges:
    #bridge_for() replacement over one shared MockLedger that remembers which (org, role) identity every call signed with.

    def __init__(self, ledger) -> None:
        self.ledger = ledger
        self.calls: List[Tuple[str, str]] = []

    def __call__(self, org: str, role: str = "official", endorsers=None):
        from mock_ledger import MockLedgerBridge
        self.calls.append((org, role))
        return MockLedgerBridge(self.ledger, org=org, role=role)


@pytest.fixture
def ledger():
    from mock_ledger import MockLedger
    return MockLedger(["org1", "org2", "org3"])


@pytest.fixture
def bridges(ledger):
    return RecordingBridges(ledger)


def _make_client(tmp_path, bridges, auth_mode: str):
    from fastapi.testclient import TestClient
    import audit
    import auth
    import server

    os.environ["AUTH_MODE"] = auth_mode
    store = OffChainStore(str(tmp_path / "offchain.db"), ipfs=FakeIpfs())
    server.STORE = store
    auth.set_server_store(store)
    auth.init_user_store(str(tmp_path / "users.db"))
    audit.init_audit(str(tmp_path / "audit.db"))
    server.PUBLIC_CACHE.clear()
    auth._login_ip_limiter._hits.clear()
    auth._login_user_limiter._hits.clear()
    original = server.bridge_for
    server.bridge_for = bridges
    client = TestClient(server.app)
    client.store = store
    client.bridges = bridges
    return client, original


@pytest.fixture
def client(tmp_path, bridges):
    import server
    c, original = _make_client(tmp_path, bridges, "jwt")
    yield c
    server.bridge_for = original


@pytest.fixture
def client_bootstrap(tmp_path, bridges):
    import server
    c, original = _make_client(tmp_path, bridges, "bootstrap")
    yield c
    server.bridge_for = original


def reset_ledger(client, orgs: int = 3, window_s: int = 72 * 3600) -> None:
    #Swap in a fresh ledger with org1..org<orgs> registered.
    from mock_ledger import MockLedger
    client.bridges.ledger = MockLedger([f"org{i}" for i in range(1, orgs + 1)], window_s=window_s)


def api_key(client, org: str = "org1", role: str = "official") -> Dict[str, str]:
    key = f"test-key-{org}-{role}"
    client.store.upsert_org_key(key, org, role=role, label=f"test-{org}-{role}")
    return {"X-API-Key": key}


def make_user(client, username: str, org: str = "org1", role: str = "fact_checker",
              password: str = PASSWORD, must_change: bool = False) -> None:
    import auth
    assert auth.get_user_store().create_user(username, org, role, password, must_change=must_change)


def login(client, username: str, password: str = PASSWORD, **extra) -> Dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": password, **extra})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def user_headers(client, username: str, org: str = "org1", role: str = "fact_checker") -> Dict[str, str]:
    make_user(client, username, org, role)
    return login(client, username)


def submit(client, headers, content: str = "A claim to check.", msg_id: str = "msg-1", label: str = "1") -> str:
    resp = client.post("/api/reports", json={
        "msg_id": msg_id, "label": label, "confidence": 0.9, "model_version": "test-v1",
        "content": content, "source_platform": "test",
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def vote(client, headers, report_id: str, outcome: str = "1", reasoning: str = "Checked the sources.",
         support=None):
    return client.post(f"/api/reports/{report_id}/fact-check", json={
        "outcome": outcome, "reasoning": reasoning, "support": support or [],
    }, headers=headers)
