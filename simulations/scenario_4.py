#!/usr/bin/env python3
"""Scenario 4 — Guards.

Every rejection the chaincode owns, and that each arrives as a readable
message rather than a nested transport error. A fact-checker's client should
be able to tell an ordinary refusal from an outage.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *  # noqa: E402,F403

title("Scenario 4 — Guards: what the ledger refuses, and how it says so")

root = new_claim()

st, r = fact_check("org1", root, "maybe")
check(st == 400, "non-binary outcome is rejected", f"HTTP {st}")
check(isinstance(r, dict) and "outcome" in str(r.get("detail", "")).lower(),
      "…with a message naming the field", str(r.get("detail"))[:40])

st, r = call("POST", GATEWAY, f"/api/reports/{root}/expire", key="key-org1")
check(st == 400, "expiry before the deadline is rejected", f"HTTP {st}")
check("fact-check window" in str(r.get("detail", "")),
      "…naming the deadline", str(r.get("detail"))[:44])

st, r = reopen("org1", root)
check(st == 400, "reopening a claim that is not FINAL is rejected", f"HTTP {st}")
check("only FINAL" in str(r.get("detail", "")),
      "…naming the required status", str(r.get("detail"))[:44])

fact_check("org1", root, "1")
st, r = fact_check("org1", root, "1")
check(st == 400, "the same org cannot check twice in one round", f"HTTP {st}")
check("already fact-checked" in str(r.get("detail", "")),
      "…naming the org and round", str(r.get("detail"))[:44])

detail = str(r.get("detail", ""))
check("gateway service error" not in detail and "chaincode response" not in detail,
      "errors are unwrapped, not nested transport blobs", "single readable sentence")

st, _ = call("GET", FACTCHECK, "/claims/pending", key="key-not-a-real-key")
check(st == 401, "an unknown API key is refused", f"HTTP {st}")

summary()
