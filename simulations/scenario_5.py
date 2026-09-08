#!/usr/bin/env python3
"""Scenario 5 — Reopening.

A settled verdict can be challenged. The panel grows, the ratio does not, and
the standing verdict stays in force for the whole round — reopening raises a
question without withdrawing the consortium's answer while it is open.

Needs a five-org consortium: round 1 requires a panel of 5.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *  # noqa: E402,F403

title("Scenario 5 — Reopening: escalation without withdrawal")

root = new_claim()
before = drive_to_final(root)
check(before["status"] == "FINAL", "claim is settled before we challenge it",
      f"final_label={before['final_label']}")

st, r = reopen("org4", root)
check(st == 200, "any registered org may reopen", f"HTTP {st}")
if st != 200:
    print(f"    {r}")
    summary()

after = chain(root)
check(after["status"] == "REOPENED", "status moves to REOPENED", after["status"])
check(after["round"] == before["round"] + 1, "the round advances",
      f"{before['round']} → {after['round']}")
check(after["required_panel"] == before["required_panel"] + 2,
      "the panel grows by two", f"{before['required_panel']} → {after['required_panel']}")
check(after["final_label"] == before["final_label"],
      "THE STANDING VERDICT IS NOT CLEARED", f"still {after['final_label']}")
check(after["fact_check_deadline"] != before["fact_check_deadline"],
      "the round gets a fresh deadline", "reset")

reopens = after.get("reopens") or []
check(len(reopens) == 1 and reopens[0]["org_msp"] == "Org4MSP",
      "the challenger is recorded on the record itself", reopens[0]["org_msp"] if reopens else "MISSING")
check(bool(reopens and reopens[0].get("tx_id")),
      "…with its own transaction id", (reopens[0].get("tx_id", "")[:16] + "…") if reopens else "")

st, r = reopen("org4", root)
check(st == 400, "the same org cannot reopen twice", f"HTTP {st}")

check(any(c.get("root_cid") == root for c in pending("org1")),
      "an org that checked in round 0 sees it again", "per-round, not per-claim")

summary()
