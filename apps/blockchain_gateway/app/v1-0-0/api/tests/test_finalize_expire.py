from __future__ import annotations
import hashlib


def _api_key_header(client, org="org1"):
    api_key = hashlib.sha256(f"test-key-{org}".encode()).hexdigest()
    client.store.upsert_org_key(api_key, org)
    return {"X-API-Key": api_key}


def _setup_report_with_fact_check(client):
    headers = _api_key_header(client)
    resp = client.post("/api/reports", json={
        "msg_id": "fin-test",
        "label": "0",
        "confidence": 0.75,
        "model_version": "v1",
        "content": "Finalize me.",
        "source_platform": "test",
    }, headers=headers)
    report_id = resp.json()["id"]

    fc_headers = _api_key_header(client, "org2")
    client.post(f"/api/reports/{report_id}/fact-check", json={
        "outcome": "1", "reasoning": "misinfo", "support": [],
    }, headers=fc_headers)

    return report_id


class TestFinalize:
    def test_finalize_sets_final_status(self, client):
        report_id = _setup_report_with_fact_check(client)
        headers = _api_key_header(client)

        resp = client.post(f"/api/reports/{report_id}/finalize", headers=headers)
        assert resp.status_code == 200

        chain_resp = client.get(f"/api/reports/{report_id}/chain", headers=headers)
        data = chain_resp.json()
        assert data["status"] == "FINAL"
        assert data["final_label"] == "1"
        assert data["finalized_by"] == "Org1MSP"

    def test_finalize_updates_off_chain_status(self, client):
        report_id = _setup_report_with_fact_check(client)
        headers = _api_key_header(client)

        client.post(f"/api/reports/{report_id}/finalize", headers=headers)

        report_resp = client.get(f"/api/reports/{report_id}", headers=headers)
        assert report_resp.json()["fact_check_status"] == "Verified_Misinformation"


class TestExpire:
    def test_expire_sets_expired_status(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/reports", json={
            "msg_id": "expire-test",
            "label": "0",
            "confidence": 0.5,
            "model_version": "v1",
            "content": "Expire me.",
            "source_platform": "test",
        }, headers=headers)
        report_id = resp.json()["id"]

        expire_resp = client.post(f"/api/reports/{report_id}/expire", headers=headers)
        assert expire_resp.status_code == 200

        chain_resp = client.get(f"/api/reports/{report_id}/chain", headers=headers)
        assert chain_resp.json()["status"] == "EXPIRED"

    def test_expire_updates_off_chain_status(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/reports", json={
            "msg_id": "expire-offchain-test",
            "label": "0",
            "confidence": 0.5,
            "model_version": "v1",
            "content": "Expire me.",
            "source_platform": "test",
        }, headers=headers)
        report_id = resp.json()["id"]

        client.post(f"/api/reports/{report_id}/expire", headers=headers)

        report_resp = client.get(f"/api/reports/{report_id}", headers=headers)
        assert report_resp.json()["fact_check_status"] == "Expired"
