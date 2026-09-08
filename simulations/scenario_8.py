#!/usr/bin/env python3
"""Scenario 8 — Integrity across versions.

The claim survives four document versions with its hash unchanged, the version
chain walks backwards to the root, and tamper-evidence still holds at the end.
This is what lets an outsider verify a document without trusting whoever
served it.

Needs a five-org consortium.
"""
import os, sys, hashlib, json as _json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *  # noqa: E402,F403

title("Scenario 8 — Integrity: one hash, four versions")

root = new_claim()
h0 = document(root)["inference_hash"]
cids = [root]

drive_to_final(root)
cids.append(chain(root)["current_cid"])
reopen("org4", root)
for org in ("org1", "org2", "org3", "org4", "org5"):
    fact_check(org, root, "1")
cids.append(chain(root)["current_cid"])

doc, rec = document(root), chain(root)

check(doc["inference_hash"] == h0,
      "inference_hash is unchanged from v1", h0[:16] + "…")
check(rec["inference_hash"] == h0, "…and matches the anchored value", "identical")
check(len(set(cids)) == len(cids), "each version produced a distinct CID", f"{len(set(cids))} distinct")
check(doc["root_cid"] == root, "root_cid never moves", root[:18] + "…")
check(rec["current_cid"] != root, "current_cid has advanced", rec["current_cid"][:18] + "…")
check(doc.get("previous_cid") not in (None, root),
      "the live version points back at its predecessor", str(doc.get("previous_cid"))[:18] + "…")

v = verify(root)
check(v["off_chain_intact"], "the document rehashes to its own stated hash", "intact")
check(v["matches_on_chain"], "…and that hash matches the ledger", "matches")
check(v["verified"], "TAMPER-EVIDENCE HOLDS AFTER FOUR VERSIONS", "verified=True")

# recompute the hash independently, the way thin-verifier.py does
core = {"source": doc.get("source"), "inference": doc.get("inference")}
canonical = _json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
recomputed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
check(recomputed == rec["inference_hash"],
      "an outsider can recompute the hash unaided", recomputed[:16] + "…")

tampered = {**doc, "source": {**doc["source"], "content": doc["source"]["content"] + " (edited)"}}
core_t = {"source": tampered["source"], "inference": tampered["inference"]}
bad = hashlib.sha256(_json.dumps(core_t, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False).encode()).hexdigest()
check(bad != rec["inference_hash"], "an edited claim fails that check", "detected")

summary()
