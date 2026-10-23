from __future__ import annotations

from tests.conftest import api_key, submit


class TestCreateReport:
    def test_submit_report_returns_id(self, client):
        resp = client.post("/api/reports", json={
            "msg_id": "test-123", "label": "0", "confidence": 0.85, "model_version": "test-model-v1",
            "content": "This is a test claim.", "source_platform": "test",
        }, headers=api_key(client))
        assert resp.status_code == 200
        assert {"id", "content_hash"} <= set(resp.json())

    def test_submit_report_stores_off_chain(self, client):
        headers = api_key(client)
        report_id = submit(client, headers, content="Another test claim.", msg_id="test-456")
        doc = client.get(f"/api/reports/{report_id}", headers=headers).json()
        assert doc["msg_id"] == "test-456"
        assert doc["source"]["content"] == "Another test claim."

    def test_submit_anchors_hash_on_chain(self, client):
        headers = api_key(client, "org2")
        report_id = submit(client, headers)
        chain = client.get(f"/api/reports/{report_id}/chain", headers=headers).json()
        doc = client.get(f"/api/reports/{report_id}", headers=headers).json()
        assert chain["content_hash"] == doc["content_hash"]
        assert chain["submitted_by"] == "Org2MSP"
        assert chain["status"] == "PENDING"

    def test_unknown_signing_org(self, client):
        resp = client.post("/api/reports", json={
            "msg_id": "m", "label": "1", "confidence": 0.5, "model_version": "v",
            "content": "c", "source_platform": "p"}, headers=api_key(client, "org42"))
        assert resp.status_code == 400

    def test_missing_report_returns_404(self, client):
        assert client.get("/api/reports/QmMissing/chain", headers=api_key(client)).status_code == 404


class TestListReports:
    def test_filter_by_status(self, client):
        headers = api_key(client)
        submit(client, headers, msg_id="a")
        submit(client, headers, content="Second claim.", msg_id="b")
        assert len(client.get("/api/reports", headers=headers).json()) == 2
        assert len(client.get("/api/reports", params={"status": "PENDING"}, headers=headers).json()) == 2
        assert client.get("/api/reports", params={"status": "FINAL"}, headers=headers).json() == []
