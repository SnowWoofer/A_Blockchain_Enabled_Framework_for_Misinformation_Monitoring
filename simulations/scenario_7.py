#!/usr/bin/env python3
"""Scenario 7 — Audit trail.

Everything the framework claims about attribution: every action carries the
org that took it and the transaction that recorded it, readable from the
record without reconstructing anything from block history.

Needs a five-org consortium.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *  # noqa: E402,F403

title("Scenario 7 — Audit trail: who did what, and in which transaction")

root = new_claim()
drive_to_final(root)
reopen("org4", root)
for org in ("org1", "org2", "org3", "org4", "org5"):
    fact_check(org, root, "1")

rec, hist = chain(root), history(root)

check(all(f.get("checker_msp") for f in rec["fact_checks"]),
      "every fact-check names its org", f"{len(rec['fact_checks'])} checks")
check(all(f.get("tx_id") for f in rec["fact_checks"]),
      "every fact-check names its transaction", "all present")
check(all("round" in f for f in rec["fact_checks"]),
      "every fact-check is stamped with its round", "all present")

rounds = sorted({f["round"] for f in rec["fact_checks"]})
check(rounds == [0, 1], "checks are grouped across both rounds", f"rounds {rounds}")
repeats = [f["checker_msp"] for f in rec["fact_checks"]]
check(len(repeats) != len(set(repeats)),
      "the same org appears in two rounds — not a duplicate", "re-examination")

reopens = rec.get("reopens") or []
check(len(reopens) == 1, "the reopen is recorded on the record", f"{len(reopens)}")
check(reopens and reopens[0].get("org_msp") and reopens[0].get("tx_id"),
      "…attributed to an org, with its transaction", reopens[0].get("org_msp", ""))

statuses = [(h.get("record") or {}).get("status") for h in hist]
check(statuses[0] == "PENDING", "history[0] is the submission", statuses[0])
check(statuses[-1] == "FINAL", "history[-1] is the settled state", statuses[-1])
check("REOPENED" in statuses, "the reopen appears in history", "present")
times = [h.get("timestamp") for h in hist]
check(times == sorted(times), "history is ordered oldest first", f"{len(hist)} transactions")
check(hist[0]["tx_id"] == rec["tx_id"],
      "record.tx_id is the creation transaction", hist[0]["tx_id"][:16] + "…")
check(all(h.get("tx_id") for h in hist), "every history entry carries a tx_id", "all present")

summary()
