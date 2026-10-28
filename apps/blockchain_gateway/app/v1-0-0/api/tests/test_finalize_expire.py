from __future__ import annotations

from tests.conftest import api_key, reset_ledger, submit, user_headers, vote


def _split_report(client):
    #6 orgs, quorum 4: two YES and one NO leaves the report open.
    reset_ledger(client, orgs=6)
    report_id = submit(client, api_key(client, "org1"))
    vote(client, api_key(client, "org2"), report_id, "1")
    vote(client, api_key(client, "org3"), report_id, "1")
    vote(client, api_key(client, "org4"), report_id, "0")
    return report_id


class TestFinalize:
    def test_finalize_blocked_while_window_open(self, client):
        report_id = _split_report(client)
        resp = client.post(f"/api/reports/{report_id}/finalize", headers=api_key(client, "org5"))
        assert resp.status_code == 400
        assert "fact-check window" in resp.json()["detail"]

    def test_finalize_after_window_by_majority(self, client):
        reset_ledger(client, orgs=6, window_s=0)
        report_id = submit(client, api_key(client, "org1"))
        vote(client, api_key(client, "org2"), report_id, "1")
        vote(client, api_key(client, "org3"), report_id, "1")
        vote(client, api_key(client, "org4"), report_id, "0")
        official = user_headers(client, "boss", "org5", "official")
        assert client.post(f"/api/reports/{report_id}/finalize", headers=official).status_code == 200
        chain = client.get(f"/api/reports/{report_id}/chain", headers=official).json()
        assert chain["status"] == "FINAL" and chain["final_label"] == "1" and chain["finalized_by"] == "Org5MSP"
        doc = client.get(f"/api/reports/{report_id}", headers=official).json()
        assert doc["fact_check_status"] == "Verified_Misinformation"


class TestExpire:
    def test_expire_blocked_while_window_open(self, client):
        report_id = submit(client, api_key(client))
        assert client.post(f"/api/reports/{report_id}/expire", headers=api_key(client)).status_code == 400

    def test_expire_after_window(self, client):
        reset_ledger(client, window_s=0)
        headers = api_key(client)
        report_id = submit(client, headers)
        assert client.post(f"/api/reports/{report_id}/expire", headers=headers).status_code == 200
        assert client.get(f"/api/reports/{report_id}/chain", headers=headers).json()["status"] == "EXPIRED"
        assert client.get(f"/api/reports/{report_id}", headers=headers).json()["fact_check_status"] == "Expired"
