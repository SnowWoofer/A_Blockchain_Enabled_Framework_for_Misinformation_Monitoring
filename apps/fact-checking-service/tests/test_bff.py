"""Integration tests: this service in front of the real blockchain gateway
app (in-process, on its mock ledger), so credential pass-through and the
gateway's authorisation are exercised for real."""
from __future__ import annotations
import hashlib
import os
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "blockchain_gateway" / "app" / "v1-0-0"
os.environ.update(BCRYPT_ROUNDS="4", COOKIE_SECURE="0", ORGS="org1,org2,org3,org4", AUTH_MODE="jwt",
                  JWT_SECRET="t" * 40, PSEUDONYM_KEY="p", IPFS_GATEWAY_URL="http://127.0.0.1:1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(GATEWAY / "api"))
sys.path.insert(0, str(GATEWAY / "src"))

from fastapi.testclient import TestClient  # noqa: E402

import audit  # noqa: E402
import auth  # noqa: E402
import server  # noqa: E402
from mock_ledger import MockLedger, MockLedgerBridge  # noqa: E402
from storage import IpfsStore, OffChainStore  # noqa: E402

from app import main as fc  # noqa: E402

PASSWORD = "correct-horse-battery"


class FakeIpfs(IpfsStore):
    def __init__(self):
        super().__init__("http://fake")
        self.blobs = {}

    def is_available(self):
        return True

    def add_bytes(self, data):
        cid = "Qm" + hashlib.sha256(data).hexdigest()[:44]
        self.blobs[cid] = data
        return cid

    def cat_bytes(self, cid, timeout=15):
        return self.blobs[cid]


@pytest.fixture
def env(tmp_path):
    store = OffChainStore(str(tmp_path / "off.db"), ipfs=FakeIpfs())
    server.STORE = store
    auth.set_server_store(store)
    auth.init_user_store(str(tmp_path / "users.db"))
    audit.init_audit(str(tmp_path / "audit.db"))
    auth._login_ip_limiter._hits.clear()
    ledger = MockLedger(["org1", "org2", "org3", "org4"])  # quorum 3, 3 eligible voters
    original = server.bridge_for
    server.bridge_for = lambda org, role="official", endorsers=None: MockLedgerBridge(ledger, org, role)
    store.upsert_org_key("ai-key", "org1", role="ai_submitter", label="ai")
    gw = TestClient(server.app)
    fc.CLIENT.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://gw")
    client = TestClient(fc.app)

    def user(name, org, role="fact_checker"):
        auth.get_user_store().create_user(name, org, role, PASSWORD, must_change=False)
        token = gw.post("/api/auth/login", json={"username": name, "password": PASSWORD}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def submit(content="Taps will run dry in Durban tonight.", msg_id="m1"):
        resp = gw.post("/api/reports", json={"msg_id": msg_id, "label": "1", "confidence": 0.8,
                                             "model_version": "m", "content": content,
                                             "source_platform": "public_tip"}, headers={"X-API-Key": "ai-key"})
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    yield client, user, submit
    server.bridge_for = original


REASON = "Municipal notice says supply continues as normal."


def test_requires_credentials(env):
    client, _, _ = env
    assert client.get("/claims/pending").status_code == 401
    assert client.get("/claims/pending", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_queue_excludes_own_org_and_already_voted(env):
    client, user, submit = env
    report_id = submit()
    org1 = user("ann", "org1")
    org2 = user("ben", "org2")
    assert client.get("/claims/pending", headers=org1).json()["total"] == 0  # org1 submitted it
    queue = client.get("/claims/pending", headers=org2).json()
    assert queue["total"] == 1
    item = queue["items"][0]
    assert item["content"] == "Taps will run dry in Durban tonight."
    assert item["can_vote"] is True and item["consensus"]["required_yes"] == 3
    assert client.post(f"/claims/{report_id}/fact-check", json={"outcome": "0", "reasoning": REASON},
                       headers=org2).status_code == 200
    assert client.get("/claims/pending", headers=org2).json()["total"] == 0
    assert client.get("/claims/reviewed", headers=org2).json()["items"][0]["my_org_vote"] == "0"


def test_blind_until_own_vote(env):
    client, user, submit = env
    report_id = submit()
    org2, org3 = user("ben", "org2"), user("cat", "org3")
    client.post(f"/claims/{report_id}/fact-check", json={"outcome": "1", "reasoning": REASON}, headers=org2)

    before = client.get(f"/claims/{report_id}", headers=org3).json()
    assert before["blind"] is True and before["fact_checks"] == []
    assert "yes" not in before["consensus"] and before["consensus"]["votes_cast"] == 1

    client.post(f"/claims/{report_id}/fact-check", json={"outcome": "1", "reasoning": REASON}, headers=org3)
    after = client.get(f"/claims/{report_id}", headers=org3).json()
    assert after["blind"] is False
    assert after["consensus"]["yes"] == 2
    assert {fc["checker_msp"] for fc in after["fact_checks"]} == {"Org2MSP", "Org3MSP"}
    assert after["fact_checks"][0]["reasoning"] == REASON


def test_votes_sign_as_the_individual_callers_org(env):
    client, user, submit = env
    report_id = submit()
    resp = client.post(f"/claims/{report_id}/fact-check", json={"outcome": "1", "reasoning": REASON},
                       headers=user("ann", "org1"))
    assert resp.status_code == 400 and "conflict of interest" in resp.json()["detail"]


def test_validation(env):
    client, user, submit = env
    report_id = submit()
    org2 = user("ben", "org2")
    assert client.post(f"/claims/{report_id}/fact-check", json={"outcome": "1", "reasoning": "too short"},
                       headers=org2).status_code == 422
    assert client.post(f"/claims/{report_id}/fact-check",
                       json={"outcome": "1", "reasoning": REASON, "support": ["javascript:alert(1)"]},
                       headers=org2).status_code == 422
    assert client.get("/claims/bad%20id", headers=org2).status_code == 400


def test_observer_can_read_but_not_vote(env):
    client, user, submit = env
    report_id = submit()
    watcher = user("obi", "org3", role="observer")
    assert client.get(f"/claims/{report_id}", headers=watcher).status_code == 200
    assert client.post(f"/claims/{report_id}/fact-check", json={"outcome": "1", "reasoning": REASON},
                       headers=watcher).status_code == 403
