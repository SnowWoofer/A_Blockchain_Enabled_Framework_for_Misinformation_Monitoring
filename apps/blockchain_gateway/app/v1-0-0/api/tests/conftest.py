from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


class MockBridge:
    def __init__(self, org: str = "org1", endorsers: Optional[List[str]] = None):
        self.org = org
        self.endorsers = endorsers or ["org1", "org2"]
        self._reports: Dict[str, Dict[str, Any]] = {}
        self._orgs: List[Dict[str, Any]] = [
            {"name": "org1", "mspid": "Org1MSP"},
            {"name": "org2", "mspid": "Org2MSP"},
            {"name": "org3", "mspid": "Org3MSP"},
        ]
        self._admissions: Dict[str, Dict[str, Any]] = {}
        self._call_log: List[tuple] = []

    def submit_report(self, report_id, content_hash, label, confidence, model_version, timestamp):
        self._call_log.append(("submit_report", report_id))
        self._reports[report_id] = {
            "id": report_id,
            "content_hash": content_hash,
            "inference_label": label,
            "confidence": confidence,
            "model_version": model_version,
            "timestamp": timestamp,
            "submitted_by": f"Org{self.org.removeprefix('org')}MSP",
            "fact_check_deadline": "2099-01-01T00:00:00Z",
            "status": "PENDING",
            "fact_checks": [],
        }
        return report_id

    def query_report(self, report_id):
        self._call_log.append(("query_report", report_id))
        if report_id not in self._reports:
            raise Exception(f"no report found for {report_id}")
        return self._reports[report_id]

    def query_all(self):
        self._call_log.append(("query_all",))
        return list(self._reports.values())

    def submit_fact_check(self, report_id, outcome):
        self._call_log.append(("submit_fact_check", report_id, outcome))
        rec = self._reports.get(report_id)
        if not rec:
            raise Exception(f"no report found for {report_id}")
        if rec["status"] not in ("PENDING", "UNDER_REVIEW"):
            raise Exception(f"report {report_id} is {rec['status']}; cannot accept fact-check")
        msp = f"Org{self.org.removeprefix('org')}MSP"
        for fc in rec["fact_checks"]:
            if fc["checker_msp"] == msp:
                raise Exception(f"{msp} already voted on {report_id}")
        rec["fact_checks"].append({
            "checker_msp": msp,
            "outcome": outcome,
            "txid": hashlib.sha256(f"{report_id}|{msp}|{outcome}".encode()).hexdigest(),
        })
        rec["status"] = "UNDER_REVIEW"
        return "ok"

    def finalize_report(self, report_id):
        self._call_log.append(("finalize_report", report_id))
        rec = self._reports.get(report_id)
        if not rec:
            raise Exception(f"no report found for {report_id}")
        if len(rec["fact_checks"]) < 1:
            raise Exception("not enough fact-checks to finalize")
        outcomes = [fc["outcome"] for fc in rec["fact_checks"]]
        winner = max(set(outcomes), key=outcomes.count)
        rec["final_label"] = winner
        rec["finalized_by"] = f"Org{self.org.removeprefix('org')}MSP"
        rec["finalized_at"] = "2026-01-01T00:00:00Z"
        rec["status"] = "FINAL"
        return "ok"

    def expire_report(self, report_id):
        self._call_log.append(("expire_report", report_id))
        rec = self._reports.get(report_id)
        if not rec:
            raise Exception(f"no report found for {report_id}")
        if rec["status"] not in ("PENDING", "UNDER_REVIEW"):
            raise Exception(f"report {report_id} is {rec['status']}; cannot expire")
        rec["status"] = "EXPIRED"
        rec["finalized_by"] = f"Org{self.org.removeprefix('org')}MSP"
        rec["finalized_at"] = "2026-01-01T00:00:00Z"
        return "ok"

    def history(self, report_id):
        self._call_log.append(("history", report_id))
        return []

    def list_orgs(self):
        self._call_log.append(("list_orgs",))
        return self._orgs

    def request_admission(self, org_name, role):
        self._call_log.append(("request_admission", org_name))
        self._admissions[org_name] = {"status": "PENDING", "votes": []}
        return org_name

    def vote_on_admission(self, msp, verdict):
        self._call_log.append(("vote_on_admission", msp, verdict))
        return "ok"

    def finalize_admission(self, msp):
        self._call_log.append(("finalize_admission", msp))
        return "ok"

    def query_admission(self, msp):
        self._call_log.append(("query_admission", msp))
        return self._admissions.get(msp, {"status": "NONE"})


@pytest.fixture
def mock_bridge():
    return MockBridge()


@pytest.fixture
def mock_bridge_for():
    bridges = {}

    def factory(org="org1"):
        if org not in bridges:
            bridges[org] = MockBridge(org=org)
        return bridges[org]

    return factory


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_offchain.db")


@pytest.fixture
def tmp_user_db(tmp_path):
    return str(tmp_path / "test_users.db")


@pytest.fixture
def app(mock_bridge, tmp_db, tmp_user_db):
    os.environ["OFFCHAIN_DB"] = tmp_db
    os.environ["USER_DB"] = tmp_user_db
    os.environ["AUTH_MODE"] = "bootstrap"
    os.environ["ORGS"] = "org1,org2,org3"

    with patch_server_deps(mock_bridge, tmp_db, tmp_user_db) as (a, store):
        yield a, store


@pytest.fixture
def app_jwt(mock_bridge, tmp_db, tmp_user_db):
    os.environ["OFFCHAIN_DB"] = tmp_db
    os.environ["USER_DB"] = tmp_user_db
    os.environ["AUTH_MODE"] = "jwt"
    os.environ["ORGS"] = "org1,org2,org3"

    with patch_server_deps(mock_bridge, tmp_db, tmp_user_db) as (a, store):
        yield a, store


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    app_obj, store = app
    _client = TestClient(app_obj)
    _client.store = store
    return _client


@pytest.fixture
def client_jwt(app_jwt):
    from fastapi.testclient import TestClient
    app_obj, store = app_jwt
    _client = TestClient(app_obj)
    _client.store = store
    return _client


import contextlib
from unittest.mock import patch


@contextlib.contextmanager
def patch_server_deps(mock_bridge, tmp_db, tmp_user_db):
    from storage import OffChainStore
    from auth import init_user_store, set_server_store

    store = OffChainStore(tmp_db)
    init_user_store(tmp_user_db)
    set_server_store(store)

    import server
    server.STORE = store

    original_bridge_for = server.bridge_for
    server.bridge_for = lambda org, endorsers=None: mock_bridge

    try:
        yield server.app, store
    finally:
        server.bridge_for = original_bridge_for
