#!/usr/bin/env python3
"""Scenario 1 — Registration.

A claim ingested as raw text reaches the ledger with the model's verdict
attached, hashed, and anchored. Establishes that the AI triages rather than
filters: the claim is registered whatever the model concluded.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *  # noqa: E402,F403

title("Scenario 1 — Registration: ingest → inference → ledger")

root = new_claim()
doc, rec = document(root), chain(root)

check(root.startswith("Qm"), "root_cid is an IPFS CID", root[:22] + "…")
check(doc.get("ingest_id", "").startswith("test_data_"),
      "ingest_id carried through from claims.raw", doc.get("ingest_id", "")[:24] + "…")
check(doc["inference"]["label"] in ("0", "1"),
      "model verdict is the ledger's binary domain", f"label={doc['inference']['label']}")
check(doc["inference"]["confidence"] >= 0.5,
      "confidence is the predicted class's probability", f"{doc['inference']['confidence']:.6f}")
check(doc.get("previous_cid") is None,
      "v1 is the head of the version chain", "previous_cid=None")
check(rec["status"] == "PENDING", "ledger status on registration", rec["status"])
check(rec["round"] == 0 and rec["required_panel"] == 3,
      "opens at round 0, panel 3", f"round={rec['round']} panel={rec['required_panel']}")
check(rec["current_cid"] == rec["root_cid"],
      "current_cid equals root_cid at v1", "identical")
check(rec["inference_hash"] == doc["inference_hash"],
      "anchored hash matches the document's", rec["inference_hash"][:16] + "…")
check(len(rec["inference_hash"]) == 64,
      "inference_hash is a sha256 hex digest", "64 chars")
check(verify(root)["verified"], "tamper-evidence check passes", "verified=True")

summary()
