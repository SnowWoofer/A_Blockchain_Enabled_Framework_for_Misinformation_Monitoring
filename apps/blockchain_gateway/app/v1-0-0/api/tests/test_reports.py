from __future__ import annotations
import hashlib


def _api_key_header(client, org="org1"):
    api_key = hashlib.sha256(f"test-key-{org}".encode()).hexdigest()
    client.store.upsert_org_key(api_key, org)
    return {"X-API-Key": api_key}


class TestCreateReport:
    def test_submit_report_returns_id(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/reports", json={
            "msg_id": "test-123",
            "label": "0",
            "confidence": 0.85,
            "model_version": "test-model-v1",
            "content": "This is a test claim.",
            "source_platform": "test",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "content_hash" in data

    def test_submit_report_stores_off_chain(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/reports", json={
            "msg_id": "test-456",
            "label": "1",
            "confidence": 0.92,
            "model_version": "test-model-v1",
            "content": "Another test claim.",
            "source_platform": "test",
        }, headers=headers)
        report_id = resp.json()["id"]
        get_resp = client.get(f"/api/reports/{report_id}", headers=headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["msg_id"] == "test-456"

    def test_submit_report_invalid_confidence(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/reports", json={
            "msg_id": "test-bad",
            "label": "0",
            "confidence": 1.5,
            "model_version": "test-model-v1",
            "content": "Bad confidence.",
            "source_platform": "test",
        }, headers=headers)
        assert resp.status_code == 422


class TestGetReport:
    def test_get_existing_report(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/reports", json={
            "msg_id": "get-test",
            "label": "0",
            "confidence": 0.7,
            "model_version": "v1",
            "content": "Get me.",
            "source_platform": "test",
        }, headers=headers)
        report_id = resp.json()["id"]
        get_resp = client.get(f"/api/reports/{report_id}", headers=headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["source"]["content"] == "Get me."

    def test_get_missing_report_returns_404(self, client):
        headers = _api_key_header(client)
        resp = client.get("/api/reports/nonexistent", headers=headers)
        assert resp.status_code == 404


class TestListReports:
    def test_list_reports(self, client):
        headers = _api_key_header(client)
        resp = client.get("/api/reports", headers=headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestChainRecord:
    def test_get_chain_record(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/reports", json={
            "msg_id": "chain-test",
            "label": "0",
            "confidence": 0.6,
            "model_version": "v1",
            "content": "Chain record test.",
            "source_platform": "test",
        }, headers=headers)
        report_id = resp.json()["id"]
        chain_resp = client.get(f"/api/reports/{report_id}/chain", headers=headers)
        assert chain_resp.status_code == 200
        data = chain_resp.json()
        assert data["id"] == report_id
        assert data["status"] == "PENDING"
