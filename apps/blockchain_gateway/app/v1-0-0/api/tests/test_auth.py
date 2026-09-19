from __future__ import annotations
import hashlib


class TestBootstrapAuth:
    def test_valid_api_key_returns_200(self, client):
        api_key = hashlib.sha256(b"test-key-org1").hexdigest()
        client.store.upsert_org_key(api_key, "org1")
        resp = client.get("/api/status", headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_missing_api_key_returns_401(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 401

    def test_invalid_api_key_returns_401(self, client):
        resp = client.get("/api/status", headers={"X-API-Key": "bad-key"})
        assert resp.status_code == 401
        assert "unknown API key" in resp.json()["detail"]


class TestJWTAuth:
    def test_register_and_login(self, client_jwt):
        resp = client_jwt.post("/api/auth/register", json={"org": "org1", "password": "secret"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp = client_jwt.post("/api/auth/login", json={"org": "org1", "password": "secret"})
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_login_with_wrong_password_returns_401(self, client_jwt):
        client_jwt.post("/api/auth/register", json={"org": "org1", "password": "secret"})
        resp = client_jwt.post("/api/auth/login", json={"org": "org1", "password": "wrong"})
        assert resp.status_code == 401

    def test_register_duplicate_returns_409(self, client_jwt):
        client_jwt.post("/api/auth/register", json={"org": "org1", "password": "secret"})
        resp = client_jwt.post("/api/auth/register", json={"org": "org1", "password": "other"})
        assert resp.status_code == 409

    def test_jwt_token_authenticates_request(self, client_jwt):
        client_jwt.post("/api/auth/register", json={"org": "org1", "password": "secret"})
        login_resp = client_jwt.post("/api/auth/login", json={"org": "org1", "password": "secret"})
        token = login_resp.json()["token"]
        resp = client_jwt.get("/api/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_invalid_jwt_token_returns_401(self, client_jwt):
        resp = client_jwt.get("/api/status", headers={"Authorization": "Bearer invalid.token.here"})
        assert resp.status_code == 401

    def test_missing_auth_header_returns_401(self, client_jwt):
        resp = client_jwt.get("/api/status")
        assert resp.status_code == 401

    def test_register_disabled_in_bootstrap_mode(self, client):
        resp = client.post("/api/auth/register", json={"org": "org1", "password": "secret"})
        assert resp.status_code == 400
        assert "JWT auth mode" in resp.json()["detail"]

    def test_login_disabled_in_bootstrap_mode(self, client):
        resp = client.post("/api/auth/login", json={"org": "org1", "password": "secret"})
        assert resp.status_code == 400
        assert "JWT auth mode" in resp.json()["detail"]
