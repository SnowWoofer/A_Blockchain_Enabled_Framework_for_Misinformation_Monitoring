#!/usr/bin/env python3
"""Scenario 6 — Overturning a verdict.

The point of the reopen mechanism: two organisations can close a claim
quickly, but it takes four to overturn them. Round 0 settles one way, round 1
settles the other, and the ledger records both.

Needs a five-org consortium.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *  # noqa: E402,F403

title("Scenario 6 — Overturn: four of five reverse a two-of-three verdict")

root = new_claim()
r0 = drive_to_final(root, outcomes=("1", "1", "0"))
check(r0["final_label"] == "1", "round 0 settles on misinformation", "2–1 → 1")

st, _ = reopen("org5", root)
check(st == 200, "claim reopened at a panel of 5", f"HTTP {st}")

# four say non-misinformation, one dissents
for org, outcome in (("org1", "0"), ("org2", "0"), ("org3", "0"), ("org4", "0")):
    st, resp = fact_check(org, root, outcome)
    if st != 200:
        check(False, f"{org} could not check", f"HTTP {st} {str(resp)[:40]}")
rec = chain(root)
n = len([f for f in rec["fact_checks"] if f["round"] == rec["round"]])
# REOPENED means "reopened, nothing checked yet this round". The first check of
# the new round moves it back to UNDER_REVIEW, exactly as in round 0 — the
# status tracks review progress, not which round we are in.
check(n == 4 and rec["status"] == "UNDER_REVIEW",
      "four checks are still below the panel of 5", f"{n}/5 status={rec['status']}")
check(rec["final_label"] == "1", "the old verdict still stands mid-round", "still 1")

st, resp = fact_check("org5", root, "1")
check(st == 200, "the fifth org can check", f"HTTP {st}")

final = chain(root)
check(final["status"] == "FINAL", "the panel is reached and the round settles", final["status"])
check(final["final_label"] == "0", "THE VERDICT IS OVERTURNED", "1 → 0")
check(final["round"] == 1, "settled in round 1", f"round={final['round']}")

r1 = [f for f in final["fact_checks"] if f["round"] == 1]
check(len(r1) == 5, "five checks recorded for round 1", f"{len(r1)}")
check(sum(1 for f in r1 if f["outcome"] == "0") == 4,
      "four of five carried the new outcome", "4–1")
check(len([f for f in final["fact_checks"] if f["round"] == 0]) == 3,
      "round 0's checks are retained, not replaced", "3 kept")
check(document(root)["fact_check_status"] == "Verified_Non_Misinformation",
      "the off-chain document reflects the new verdict",
      document(root)["fact_check_status"])

summary()
