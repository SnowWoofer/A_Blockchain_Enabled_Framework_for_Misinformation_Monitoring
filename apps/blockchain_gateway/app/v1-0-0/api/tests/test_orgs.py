from __future__ import annotations
import hashlib


def _api_key_header(client, org="org1"):
    api_key = hashlib.sha256(f"test-key-{org}".encode()).hexdigest()
    client.store.upsert_org_key(api_key, org)
    return {"X-API-Key": api_key}


class TestListOrgs:
    def test_list_orgs(self, client):
        headers = _api_key_header(client)
        resp = client.get("/api/orgs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1


class TestAdmission:
    def test_request_admission(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/orgs/org4/admission", headers=headers)
        assert resp.status_code == 200
        assert "candidate_msp" in resp.json()

    def test_vote_admission(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/orgs/org4/admission/vote", json={"verdict": "admit"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_finalize_admission(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/orgs/org4/admission/finalize", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_get_admission(self, client):
        headers = _api_key_header(client)
        resp = client.get("/api/orgs/org4/admission", headers=headers)
        assert resp.status_code == 200


class TestStatus:
    def test_status(self, client):
        headers = _api_key_header(client)
        resp = client.get("/api/status", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "ipfs_available" in data
        assert "crypto_style" in data
