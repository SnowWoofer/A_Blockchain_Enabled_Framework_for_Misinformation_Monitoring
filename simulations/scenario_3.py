#!/usr/bin/env python3
"""Scenario 3 — Round 0 consensus.

A claim closes on a two-thirds supermajority of a three-org panel. Because the
panel is odd and the outcome binary, no tie is possible — there is no
tie-breaking path in the system to exercise.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *  # noqa: E402,F403

title("Scenario 3 — Round 0: panel of 3, two-thirds supermajority")

root = new_claim()

fact_check("org1", root, "1")
rec = chain(root)
check(rec["status"] == "UNDER_REVIEW", "one check does not settle it", rec["status"])

fact_check("org2", root, "1")
rec = chain(root)
check(rec["status"] == "UNDER_REVIEW", "two checks are still below the panel",
      f"{len([f for f in rec['fact_checks'] if f['round'] == 0])}/3")

fact_check("org3", root, "0")
rec = chain(root)
check(rec["status"] == "FINAL", "the third check settles it at 2–1", rec["status"])
check(rec["final_label"] == "1", "majority outcome wins", f"final_label={rec['final_label']}")
check(rec["finalized_by"] == "Org3MSP",
      "finalized_by is the org whose check closed it", rec["finalized_by"])
check(len([f for f in rec["fact_checks"] if f["round"] == 0]) == 3,
      "exactly three checks in round 0", "3")
check(all(f["outcome"] in ("0", "1") for f in rec["fact_checks"]),
      "every outcome is in the binary domain", "0/1 only")
check(rec["current_cid"] != rec["root_cid"],
      "the verdict published a new document version", "current_cid advanced")
check(document(root)["fact_check_status"].startswith("Verified"),
      "off-chain status reflects the verdict", document(root)["fact_check_status"])

summary()
