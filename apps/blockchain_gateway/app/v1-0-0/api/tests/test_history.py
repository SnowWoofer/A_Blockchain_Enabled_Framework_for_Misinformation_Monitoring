from __future__ import annotations

from tests.conftest import api_key, submit


class TestHistory:
    def test_history_records_each_transaction(self, client):
        report_id = submit(client, api_key(client, "org1"))
        client.post(f"/api/reports/{report_id}/fact-check", json={"outcome": "1", "reasoning": "r"},
                    headers=api_key(client, "org2"))
        history = client.get(f"/api/reports/{report_id}/history", headers=api_key(client)).json()
        assert [h["record"]["status"] for h in history] == ["PENDING", "UNDER_REVIEW"]
        assert all(len(h["tx_id"]) == 64 for h in history)
