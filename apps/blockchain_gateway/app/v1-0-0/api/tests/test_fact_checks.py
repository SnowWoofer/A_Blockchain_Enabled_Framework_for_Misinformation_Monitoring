from __future__ import annotations
import hashlib


def _api_key_header(client, org="org1"):
    api_key = hashlib.sha256(f"test-key-{org}".encode()).hexdigest()
    client.store.upsert_org_key(api_key, org)
    return {"X-API-Key": api_key}


def _setup_report(client, headers):
    resp = client.post("/api/reports", json={
        "msg_id": "fc-test",
        "label": "0",
        "confidence": 0.8,
        "model_version": "v1",
        "content": "Fact-check me.",
        "source_platform": "test",
    }, headers=headers)
    return resp.json()["id"]


class TestSubmitFactCheck:
    def test_fact_check_transitions_to_under_review(self, client):
        headers = _api_key_header(client)
        report_id = _setup_report(client, headers)

        fc_headers = _api_key_header(client, "org2")
        resp = client.post(f"/api/reports/{report_id}/fact-check", json={
            "outcome": "1",
            "reasoning": "This is misinformation.",
            "support": ["https://example.com"],
        }, headers=fc_headers)
        assert resp.status_code == 200

        chain_resp = client.get(f"/api/reports/{report_id}/chain", headers=headers)
        assert chain_resp.json()["status"] == "UNDER_REVIEW"
        assert len(chain_resp.json()["fact_checks"]) == 1

    def test_duplicate_vote_rejected(self, client):
        headers = _api_key_header(client)
        report_id = _setup_report(client, headers)

        fc_headers = _api_key_header(client, "org2")
        client.post(f"/api/reports/{report_id}/fact-check", json={
            "outcome": "1", "reasoning": "first", "support": [],
        }, headers=fc_headers)

        resp = client.post(f"/api/reports/{report_id}/fact-check", json={
            "outcome": "0", "reasoning": "second", "support": [],
        }, headers=fc_headers)
        assert resp.status_code == 400

    def test_fact_check_on_missing_report_returns_404(self, client):
        fc_headers = _api_key_header(client, "org2")
        resp = client.post("/api/reports/nonexistent/fact-check", json={
            "outcome": "1", "reasoning": "", "support": [],
        }, headers=fc_headers)
        assert resp.status_code == 404
