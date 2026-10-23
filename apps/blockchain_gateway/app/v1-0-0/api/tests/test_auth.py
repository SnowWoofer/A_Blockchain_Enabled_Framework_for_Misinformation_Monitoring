from __future__ import annotations
import sqlite3

import auth
import totp
from tests.conftest import PASSWORD, api_key, login, make_user, user_headers

XHR = {"X-Requested-With": "XMLHttpRequest"}


class TestApiKeys:
    def test_valid_api_key_returns_200(self, client):
        resp = client.get("/api/status", headers=api_key(client))
        assert resp.status_code == 200

    def test_missing_credentials_returns_401(self, client):
        assert client.get("/api/status").status_code == 401

    def test_invalid_api_key_returns_401(self, client):
        resp = client.get("/api/status", headers={"X-API-Key": "bad-key"})
        assert resp.status_code == 401
        assert "unknown API key" in resp.json()["detail"]

    def test_keys_are_stored_hashed(self, client):
        client.store.upsert_org_key("super-secret-key", "org1")
        rows = client.store._conn.execute("SELECT key_hash FROM api_keys").fetchall()
        assert all("super-secret-key" not in r["key_hash"] for r in rows)
        assert client.store.org_for_key("super-secret-key") == "org1"

    def test_revoked_key_rejected(self, client):
        headers = api_key(client)
        client.store.revoke_keys(api_key=headers["X-API-Key"])
        assert client.get("/api/status", headers=headers).status_code == 401

    def test_legacy_plaintext_keys_are_migrated(self, tmp_path):
        from storage import OffChainStore
        from tests.conftest import FakeIpfs
        db = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE org_keys (api_key TEXT PRIMARY KEY, org TEXT NOT NULL)")
        conn.execute("INSERT INTO org_keys VALUES ('key-org2', 'org2')")
        conn.commit()
        conn.close()
        store = OffChainStore(db, ipfs=FakeIpfs())
        assert store.principal_for_key("key-org2") == ("org2", "official", "migrated")
        tables = {r[0] for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "org_keys" not in tables

    def test_api_keys_work_in_bootstrap_mode(self, client_bootstrap):
        assert client_bootstrap.get("/api/status", headers=api_key(client_bootstrap)).status_code == 200


class TestLogin:
    def test_login_and_me(self, client):
        headers = user_headers(client, "alice", "org2", "fact_checker")
        me = client.get("/api/auth/me", headers=headers).json()
        assert me["org"] == "org2" and me["role"] == "fact_checker" and me["mspid"] == "Org2MSP"
        assert me["kind"] == "user"

    def test_wrong_password_and_unknown_user_look_the_same(self, client):
        make_user(client, "alice")
        a = client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password!"})
        b = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong-password!"})
        assert a.status_code == b.status_code == 401
        assert a.json() == b.json() == {"detail": "invalid credentials"}

    def test_lockout_after_repeated_failures(self, client):
        make_user(client, "alice")
        for _ in range(auth.MAX_FAILED_LOGINS):
            client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password!"})
        resp = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        assert resp.status_code == 423

    def test_disabled_user_cannot_login(self, client):
        make_user(client, "alice")
        auth.get_user_store().update_user("alice", active=0)
        resp = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        assert resp.status_code == 401

    def test_login_rate_limited(self, client):
        for _ in range(10):
            client.post("/api/auth/login", json={"username": "victim", "password": "guess-guess-guess"})
        resp = client.post("/api/auth/login", json={"username": "victim", "password": "guess-guess-guess"})
        assert resp.status_code == 429

    def test_login_disabled_in_bootstrap_mode(self, client_bootstrap):
        resp = client_bootstrap.post("/api/auth/login", json={"username": "a", "password": "b"})
        assert resp.status_code == 400
        assert "JWT auth mode" in resp.json()["detail"]

    def test_forged_token_rejected(self, client):
        import jwt
        forged = jwt.encode({"sub": "alice", "org": "org1", "role": "official", "sid": "x",
                             "iat": 0, "exp": 9999999999, "iss": auth.JWT_ISSUER, "aud": auth.JWT_AUDIENCE},
                            "not-the-secret", algorithm="HS256")
        resp = client.get("/api/status", headers={"Authorization": f"Bearer {forged}"})
        assert resp.status_code == 401

    def test_role_change_invalidates_token(self, client):
        headers = user_headers(client, "alice", "org1", "official")
        auth.get_user_store().update_user("alice", role="observer")
        assert client.get("/api/auth/me", headers=headers).status_code == 401


class TestSessions:
    def test_refresh_rotates_and_detects_reuse(self, client, monkeypatch):
        make_user(client, "alice")
        client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        first = client.cookies.get(auth.REFRESH_COOKIE)
        resp = client.post("/api/auth/refresh", headers=XHR)
        assert resp.status_code == 200
        second = client.cookies.get(auth.REFRESH_COOKIE)
        assert second and second != first
        access = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        assert client.get("/api/auth/me", headers=access).status_code == 200

        # replaying the rotated token (as a thief would) ends the session
        client.cookies.clear()
        client.cookies.set(auth.REFRESH_COOKIE, first)
        monkeypatch.setattr(auth, "REFRESH_REUSE_GRACE_S", 0)
        assert client.post("/api/auth/refresh", headers=XHR).status_code == 401
        assert client.get("/api/auth/me", headers=access).status_code == 401

    def test_concurrent_refresh_within_grace_is_not_theft(self, client):
        make_user(client, "alice")
        client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        first = client.cookies.get(auth.REFRESH_COOKIE)
        assert client.post("/api/auth/refresh", headers=XHR).status_code == 200
        # a second tab sent the same (now rotated) cookie a moment later
        client.cookies.clear()
        client.cookies.set(auth.REFRESH_COOKIE, first)
        resp = client.post("/api/auth/refresh", headers=XHR)
        assert resp.status_code == 200
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"}).status_code == 200

    def test_refresh_requires_xhr_header(self, client):
        make_user(client, "alice")
        client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        assert client.post("/api/auth/refresh").status_code == 403

    def test_logout_revokes_access_token(self, client):
        headers = user_headers(client, "alice")
        assert client.post("/api/auth/logout", headers={**headers, **XHR}).status_code == 200
        assert client.get("/api/auth/me", headers=headers).status_code == 401


class TestPasswords:
    def test_must_change_password_blocks_other_routes(self, client):
        make_user(client, "alice", must_change=True)
        headers = login(client, "alice")
        assert client.get("/api/orgs", headers=headers).status_code == 403
        resp = client.post("/api/auth/password", json={
            "current_password": PASSWORD, "new_password": "a-much-better-passphrase"}, headers=headers)
        assert resp.status_code == 200
        assert client.get("/api/orgs", headers=headers).status_code == 200

    def test_weak_password_rejected(self, client):
        headers = user_headers(client, "alice")
        resp = client.post("/api/auth/password", json={
            "current_password": PASSWORD, "new_password": "short"}, headers=headers)
        assert resp.status_code == 400
        assert "12 characters" in resp.json()["detail"]

    def test_policy(self):
        assert auth.password_problems("alice-has-a-long-pass", "alice")
        assert auth.password_problems("aaaaaaaaaaaaaaaa")
        assert not auth.password_problems("violet staple orbit 42")


class TestMfa:
    def test_enable_then_login_requires_code(self, client):
        headers = user_headers(client, "alice")
        secret = client.post("/api/auth/mfa/setup", headers=headers).json()["secret"]
        assert client.post("/api/auth/mfa/enable", json={"code": "000000"}, headers=headers).status_code == 400
        assert client.post("/api/auth/mfa/enable", json={"code": totp.code_at(secret)},
                           headers=headers).status_code == 200

        resp = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        assert resp.status_code == 401 and resp.json()["detail"]["code"] == "mfa_required"
        # the code used to enable is already spent, so wait for the next step
        next_code = totp.code_at(secret, now=(totp.current_step() + 1) * totp.STEP_S)
        resp = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD, "otp": next_code})
        assert resp.status_code == 200
        replay = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD, "otp": next_code})
        assert replay.status_code == 401 and replay.json()["detail"]["code"] == "mfa_invalid"

    def test_totp_known_vector(self):
        # RFC 6238 appendix B, SHA-1, T=59 -> 94287082 (8 digits); last 6 digits
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        assert totp.code_at(secret, now=59) == "287082"


class TestUserAdmin:
    def test_official_manages_own_org_only(self, client):
        boss = user_headers(client, "boss", "org2", "official")
        resp = client.post("/api/users", json={"username": "newbie", "role": "fact_checker"}, headers=boss)
        assert resp.status_code == 200
        temp = resp.json()["temporary_password"]
        assert resp.json()["user"]["org"] == "org2"
        assert resp.json()["user"]["must_change_password"] is True
        assert client.post("/api/auth/login", json={"username": "newbie", "password": temp}).status_code == 200

        make_user(client, "outsider", "org3")
        assert client.patch("/api/users/outsider", json={"active": False}, headers=boss).status_code == 404
        assert [u["username"] for u in client.get("/api/users", headers=boss).json()] == ["boss", "newbie"]

    def test_fact_checker_cannot_manage_users(self, client):
        headers = user_headers(client, "alice", "org1", "fact_checker")
        assert client.post("/api/users", json={"username": "x-y-z", "role": "official"},
                           headers=headers).status_code == 403

    def test_api_key_cannot_manage_users(self, client):
        resp = client.post("/api/users", json={"username": "x-y-z", "role": "official"}, headers=api_key(client))
        assert resp.status_code == 403

    def test_cannot_disable_self(self, client):
        boss = user_headers(client, "boss", "org1", "official")
        assert client.patch("/api/users/boss", json={"active": False}, headers=boss).status_code == 400

    def test_disabling_user_ends_sessions(self, client):
        boss = user_headers(client, "boss", "org1", "official")
        alice = user_headers(client, "alice", "org1", "fact_checker")
        assert client.patch("/api/users/alice", json={"active": False}, headers=boss).status_code == 200
        assert client.get("/api/auth/me", headers=alice).status_code == 401

    def test_reset_issues_one_time_password(self, client):
        boss = user_headers(client, "boss", "org1", "official")
        make_user(client, "alice")
        temp = client.post("/api/users/alice/reset", headers=boss).json()["temporary_password"]
        headers = login(client, "alice", temp)
        assert client.get("/api/auth/me", headers=headers).json()["must_change_password"] is True
