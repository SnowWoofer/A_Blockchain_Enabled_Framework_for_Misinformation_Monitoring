from __future__ import annotations
import hashlib


def _api_key_header(client, org="org1"):
    api_key = hashlib.sha256(f"test-key-{org}".encode()).hexdigest()
    client.store.upsert_org_key(api_key, org)
    return {"X-API-Key": api_key}


class TestVerify:
    def test_verify_intact_report(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/reports", json={
            "msg_id": "verify-test",
            "label": "0",
            "confidence": 0.8,
            "model_version": "v1",
            "content": "Verify me.",
            "source_platform": "test",
        }, headers=headers)
        report_id = resp.json()["id"]

        verify_resp = client.get(f"/api/reports/{report_id}/verify", headers=headers)
        assert verify_resp.status_code == 200
        data = verify_resp.json()
        assert data["verified"] is True
        assert data["off_chain_intact"] is True
        assert data["matches_on_chain"] is True

    def test_verify_missing_report_returns_404(self, client):
        headers = _api_key_header(client)
        resp = client.get("/api/reports/nonexistent/verify", headers=headers)
        assert resp.status_code == 404
