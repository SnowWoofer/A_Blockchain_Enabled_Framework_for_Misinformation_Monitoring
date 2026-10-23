from __future__ import annotations

from tests.conftest import api_key, submit, user_headers, vote


def _closed_report(client, content="Polling stations close at noon on election day.", msg_id="tip-1"):
    report_id = submit(client, api_key(client, "org1"), content=content, msg_id=msg_id)
    vote(client, api_key(client, "org2"), report_id, "1", "The commission confirmed 7pm closing.",
         ["https://example.org/notice"])
    vote(client, api_key(client, "org3"), report_id, "1", "Matches the gazette.")
    return report_id


class TestPublicReports:
    def test_only_closed_reports_are_published(self, client):
        closed = _closed_report(client)
        pending = submit(client, api_key(client), content="Unverified rumour.", msg_id="tip-2")
        items = client.get("/api/public/reports").json()["items"]
        assert [i["id"] for i in items] == [closed]
        assert client.get(f"/api/public/reports/{pending}").status_code == 404

    def test_detail_has_verdict_reasoning_and_integrity(self, client):
        report_id = _closed_report(client)
        data = client.get(f"/api/public/reports/{report_id}").json()
        assert data["verdict"] == "misinformation"
        assert data["votes"] == {"cast": 2, "yes": 2, "no": 0, "required_yes": 2, "registered_orgs": 3}
        assert {fc["org"] for fc in data["fact_checks"]} == {"Org2MSP", "Org3MSP"}
        assert data["fact_checks"][0]["evidence"] == ["https://example.org/notice"]
        assert data["integrity"]["verified"] is True
        assert [h["status"] for h in data["history"]] == ["PENDING", "UNDER_REVIEW", "FINAL"]
        assert "reviewer_ref" not in str(data)

    def test_search(self, client):
        target = _closed_report(client, content="Ballot papers ran out in Polokwane.", msg_id="a")
        _closed_report(client, content="Vaccines contain microchips.", msg_id="b")
        hits = client.get("/api/public/reports", params={"q": "polokwane ballot"}).json()["items"]
        assert [h["id"] for h in hits] == [target]
        # FTS syntax in user input is treated as plain words
        assert client.get("/api/public/reports", params={"q": 'NEAR(" OR *'}).status_code == 200

    def test_filter_by_verdict(self, client):
        _closed_report(client)
        assert client.get("/api/public/reports", params={"verdict": "not_confirmed"}).json()["total"] == 0
        assert client.get("/api/public/reports", params={"verdict": "misinformation"}).json()["total"] == 1

    def test_stats_and_info(self, client):
        _closed_report(client)
        submit(client, api_key(client), content="Still open.", msg_id="x")
        stats = client.get("/api/public/stats").json()
        assert stats["total_reports"] == 2
        assert stats["by_verdict"] == {"misinformation": 1, "under_review": 1}
        info = client.get("/api/public/info").json()
        assert info["ledger_backend"] in ("auto", "mock", "gateway", "cli")
        assert info["required_yes"] == 2


class TestTipStatus:
    def test_tip_lifecycle(self, client):
        assert client.get("/api/public/tips/tip_abc").json()["state"] == "screening"
        report_id = submit(client, api_key(client), msg_id="tip_abc")
        assert client.get("/api/public/tips/tip_abc").json()["state"] == "under_review"
        vote(client, api_key(client, "org2"), report_id, "1")
        vote(client, api_key(client, "org3"), report_id, "1")
        data = client.get("/api/public/tips/tip_abc").json()
        assert data == {"reference": "tip_abc", "state": "closed", "report_id": report_id,
                        "verdict": "misinformation"}

    def test_malformed_reference(self, client):
        assert client.get("/api/public/tips/%3Cscript%3E").status_code == 400


class TestAudit:
    def test_votes_and_logins_are_audited_per_org(self, client):
        boss = user_headers(client, "boss", "org2", "official")
        report_id = submit(client, api_key(client, "org1"))
        vote(client, boss, report_id, "1")
        entries = client.get("/api/audit", headers=boss).json()
        actions = [e["action"] for e in entries]
        assert "fact_check" in actions and "login" in actions
        assert all(e["org"] == "org2" for e in entries)
        assert client.get("/api/audit/verify", headers=boss).json()["intact"] is True

    def test_tampering_breaks_the_chain(self, client):
        import audit
        boss = user_headers(client, "boss", "org1", "official")
        submit(client, boss)
        log = audit.get_audit()
        with log._conn:
            log._conn.execute("UPDATE audit_log SET actor='mallory' WHERE id=1")
        result = client.get("/api/audit/verify", headers=boss).json()
        assert result["intact"] is False and result["first_bad_id"] == 1
