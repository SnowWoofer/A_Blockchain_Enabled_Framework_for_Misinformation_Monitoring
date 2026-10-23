from __future__ import annotations
import json

from tests.conftest import api_key, submit


class TestVerify:
    def test_untampered_report_verifies(self, client):
        headers = api_key(client)
        report_id = submit(client, headers, content="Verify me.")
        data = client.get(f"/api/reports/{report_id}/verify", headers=headers).json()
        assert data["off_chain_intact"] is True
        assert data["matches_on_chain"] is True
        assert data["verified"] is True

    def test_tampered_off_chain_copy_detected(self, client):
        headers = api_key(client)
        report_id = submit(client, headers, content="Original claim text.")
        row = client.store._conn.execute(
            "SELECT cid FROM report_index WHERE report_id=?", (report_id,)).fetchone()
        ipfs = client.store._ipfs
        doc = json.loads(ipfs.blobs[row["cid"]])
        doc["source"]["content"] = "Edited after the vote."
        ipfs.blobs[row["cid"]] = json.dumps(doc).encode()
        data = client.get(f"/api/reports/{report_id}/verify", headers=headers).json()
        assert data["off_chain_intact"] is False
        assert data["verified"] is False

