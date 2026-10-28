from __future__ import annotations

from tests.conftest import api_key, user_headers


class TestListOrgs:
    def test_list_orgs(self, client):
        orgs = client.get("/api/orgs", headers=api_key(client)).json()
        assert [o["mspid"] for o in orgs] == ["Org1MSP", "Org2MSP", "Org3MSP"]


class TestAdmission:
    def test_admission_workflow(self, client):
        # org4 is not yet a registered stakeholder, it applies with its own identity
        resp = client.post("/api/orgs/Org4MSP/admission", headers=api_key(client, "org4"))
        assert resp.status_code == 200
        assert resp.json()["candidate_msp"] == "Org4MSP"
        for org in ("org1", "org2"):
            r = client.post("/api/orgs/Org4MSP/admission/vote", json={"verdict": "admit"},
                            headers=api_key(client, org))
            assert r.status_code == 200, r.text
        assert client.post("/api/orgs/Org4MSP/admission/finalize", headers=api_key(client)).status_code == 200
        status = client.get("/api/orgs/Org4MSP/admission", headers=api_key(client)).json()
        assert status["status"] == "ADMITTED"
        assert len(client.get("/api/orgs", headers=api_key(client)).json()) == 4

    def test_invalid_verdict_rejected(self, client):
        resp = client.post("/api/orgs/Org4MSP/admission/vote", json={"verdict": "maybe"}, headers=api_key(client))
        assert resp.status_code == 422

    def test_fact_checker_cannot_vote_on_admission(self, client):
        checker = user_headers(client, "checker", "org2", "fact_checker")
        resp = client.post("/api/orgs/Org4MSP/admission/vote", json={"verdict": "1"}, headers=checker)
        assert resp.status_code == 403


class TestApplyOrg:
    def test_issues_random_key_for_registered_org(self, client):
        resp = client.post("/api/orgs/apply", json={"org": "org3"}, headers=api_key(client))
        assert resp.status_code == 200
        key = resp.json()["api_key"]
        assert len(key) >= 40 and key != "key-org3"
        assert client.get("/api/status", headers={"X-API-Key": key}).status_code == 200

    def test_unregistered_org_refused(self, client):
        resp = client.post("/api/orgs/apply", json={"org": "org8"}, headers=api_key(client))
        assert resp.status_code == 400

    def test_fact_checker_cannot_mint_keys(self, client):
        checker = user_headers(client, "checker", "org2", "fact_checker")
        assert client.post("/api/orgs/apply", json={"org": "org3"}, headers=checker).status_code == 403


class TestStatus:
    def test_status(self, client):
        data = client.get("/api/status", headers=api_key(client)).json()
        assert {"ipfs_available", "crypto_style", "ledger_backend", "auth_mode"} <= set(data)

    def test_healthz_is_public(self, client):
        assert client.get("/healthz").json() == {"status": "ok"}
