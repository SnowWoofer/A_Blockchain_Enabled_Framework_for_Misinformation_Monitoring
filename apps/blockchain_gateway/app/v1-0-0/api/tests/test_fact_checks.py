from __future__ import annotations
import threading

from tests.conftest import api_key, reset_ledger, submit, user_headers, vote


class TestSubmitFactCheck:
    def test_fact_check_transitions_to_under_review(self, client):
        report_id = submit(client, api_key(client, "org1"))
        resp = vote(client, api_key(client, "org2"), report_id, "1", "This is misinformation.",
                    ["https://example.com/source"])
        assert resp.status_code == 200
        chain = client.get(f"/api/reports/{report_id}/chain", headers=api_key(client)).json()
        assert chain["status"] == "UNDER_REVIEW"
        assert [c["checker_msp"] for c in chain["fact_checks"]] == ["Org2MSP"]

    def test_quorum_finalises(self, client):
        report_id = submit(client, api_key(client, "org1"))
        vote(client, api_key(client, "org2"), report_id, "1")
        vote(client, api_key(client, "org3"), report_id, "1")
        chain = client.get(f"/api/reports/{report_id}/chain", headers=api_key(client)).json()
        assert chain["status"] == "FINAL" and chain["final_label"] == "1"

    def test_submitter_cannot_vote_on_own_report(self, client):
        report_id = submit(client, api_key(client, "org1"))
        resp = vote(client, user_headers(client, "own", "org1", "fact_checker"), report_id)
        assert resp.status_code == 400
        assert "conflict of interest" in resp.json()["detail"]

    def test_duplicate_vote_rejected(self, client):
        report_id = submit(client, api_key(client, "org1"))
        org2 = api_key(client, "org2")
        assert vote(client, org2, report_id, "1").status_code == 200
        resp = vote(client, org2, report_id, "0")
        assert resp.status_code == 400
        assert "already fact-checked" in resp.json()["detail"]

    def test_fact_check_on_missing_report_returns_404(self, client):
        assert vote(client, api_key(client, "org2"), "QmNonexistent").status_code == 404

    def test_unsafe_evidence_links_rejected(self, client):
        report_id = submit(client, api_key(client, "org1"))
        for bad in ("javascript:alert(1)", "data:text/html,hi", "ftp://x.org/a", "https://user:pw@x.org/",
                    "not a url"):
            resp = vote(client, api_key(client, "org2"), report_id, "1", support=[bad])
            assert resp.status_code == 422, bad

    def test_reasoning_is_off_chain_with_pseudonymous_reviewer(self, client):
        report_id = submit(client, api_key(client, "org1"))
        checker = user_headers(client, "carol", "org2", "fact_checker")
        vote(client, checker, report_id, "1", "Contradicted by the electoral commission.")
        doc = client.get(f"/api/reports/{report_id}", headers=checker).json()
        entry = doc["fact_checks"][0]
        assert entry["reasoning"] == "Contradicted by the electoral commission."
        assert len(entry["reviewer_ref"]) == 16
        assert "carol" not in str(doc)

    def test_concurrent_votes_keep_every_reasoning(self, client):
        reset_ledger(client, orgs=6)  # quorum 4
        report_id = submit(client, api_key(client, "org1"))
        results = []

        def cast(org):
            results.append(vote(client, api_key(client, org), report_id, "1", f"reason from {org}").status_code)

        threads = [threading.Thread(target=cast, args=(f"org{i}",)) for i in (2, 3, 4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results == [200, 200, 200]
        doc = client.get(f"/api/reports/{report_id}", headers=api_key(client)).json()
        assert sorted(fc["reasoning"] for fc in doc["fact_checks"]) == [
            "reason from org2", "reason from org3", "reason from org4"]


class TestConsensus:
    def test_summary_for_viewer(self, client):
        report_id = submit(client, api_key(client, "org1"))
        vote(client, api_key(client, "org2"), report_id, "1")
        org3 = user_headers(client, "dave", "org3", "fact_checker")
        summary = client.get(f"/api/reports/{report_id}/consensus", headers=org3).json()
        assert summary["registered_orgs"] == 3
        assert summary["required_yes"] == 2
        assert summary["eligible_voters"] == 2
        assert summary["votes_cast"] == 1 and summary["remaining_voters"] == 1
        assert summary["can_vote"] is True

        org1 = user_headers(client, "erin", "org1", "fact_checker")
        own = client.get(f"/api/reports/{report_id}/consensus", headers=org1).json()
        assert own["my_org_is_submitter"] is True and own["can_vote"] is False
