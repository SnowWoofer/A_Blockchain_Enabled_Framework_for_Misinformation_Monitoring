from __future__ import annotations
import hashlib


def _api_key_header(client, org="org1"):
    api_key = hashlib.sha256(f"test-key-{org}".encode()).hexdigest()
    client.store.upsert_org_key(api_key, org)
    return {"X-API-Key": api_key}


class TestHistory:
    def test_history_returns_list(self, client):
        headers = _api_key_header(client)
        resp = client.post("/api/reports", json={
            "msg_id": "history-test",
            "label": "0",
            "confidence": 0.7,
            "model_version": "v1",
            "content": "History test.",
            "source_platform": "test",
        }, headers=headers)
        report_id = resp.json()["id"]

        hist_resp = client.get(f"/api/reports/{report_id}/history", headers=headers)
        assert hist_resp.status_code == 200
        assert isinstance(hist_resp.json(), list)
