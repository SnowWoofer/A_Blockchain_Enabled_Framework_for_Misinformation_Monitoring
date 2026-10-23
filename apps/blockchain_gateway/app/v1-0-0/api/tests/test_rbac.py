from __future__ import annotations

from tests.conftest import api_key, submit, user_headers, vote


class TestRoleMatrix:
    def test_ai_submitter_can_only_submit(self, client):
        bot = api_key(client, "org1", "ai_submitter")
        report_id = submit(client, bot)
        assert client.get("/api/reports", headers=bot).status_code == 403
        assert client.get(f"/api/reports/{report_id}", headers=bot).status_code == 403
        assert vote(client, bot, report_id).status_code == 403

    def test_ai_submitter_signs_as_fact_checker_identity(self, client):
        submit(client, api_key(client, "org1", "ai_submitter"))
        assert ("org1", "fact_checker") in client.bridges.calls
        assert ("org1", "official") not in client.bridges.calls

    def test_observer_is_read_only(self, client):
        report_id = submit(client, api_key(client))
        watcher = user_headers(client, "watcher", "org2", "observer")
        assert client.get(f"/api/reports/{report_id}/chain", headers=watcher).status_code == 200
        assert vote(client, watcher, report_id).status_code == 403
        assert client.post("/api/reports", json={}, headers=watcher).status_code in (403, 422)

    def test_fact_checker_votes_but_cannot_govern(self, client):
        report_id = submit(client, api_key(client))
        checker = user_headers(client, "checker", "org2", "fact_checker")
        assert vote(client, checker, report_id).status_code == 200
        assert ("org2", "fact_checker") in client.bridges.calls
        for path in (f"/api/reports/{report_id}/finalize", f"/api/reports/{report_id}/expire",
                     "/api/orgs/Org4MSP/admission/finalize"):
            assert client.post(path, headers=checker).status_code == 403, path
        assert client.get("/api/audit", headers=checker).status_code == 403

    def test_official_signs_as_official_identity(self, client):
        boss = user_headers(client, "boss", "org3", "official")
        assert client.get("/api/orgs", headers=boss).status_code == 200
        assert client.bridges.calls[-1] == ("org3", "official")

    def test_denials_are_audited(self, client):
        checker = user_headers(client, "checker", "org2", "fact_checker")
        client.get("/api/audit", headers=checker)
        boss = user_headers(client, "boss", "org2", "official")
        denied = client.get("/api/audit", params={"action": "access_denied"}, headers=boss).json()
        assert denied and denied[0]["actor"] == "checker" and denied[0]["outcome"] == "denied"


class TestInputValidation:
    def test_malformed_report_id(self, client):
        headers = api_key(client)
        assert client.get("/api/reports/bad%20id/chain", headers=headers).status_code == 400
        assert client.get("/api/reports/..%2F..%2Fetc/chain", headers=headers).status_code in (400, 404)

    def test_bad_label_rejected(self, client):
        resp = client.post("/api/reports", json={
            "msg_id": "m", "label": "maybe", "confidence": 0.5, "model_version": "v",
            "content": "c", "source_platform": "p"}, headers=api_key(client))
        assert resp.status_code == 422

    def test_oversized_body_rejected(self, client):
        resp = client.post("/api/reports", content=b"{" + b" " * 300_000 + b"}",
                           headers={**api_key(client), "Content-Type": "application/json"})
        assert resp.status_code == 413

    def test_security_headers(self, client):
        resp = client.get("/api/status", headers=api_key(client))
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in resp.headers["Content-Security-Policy"]
