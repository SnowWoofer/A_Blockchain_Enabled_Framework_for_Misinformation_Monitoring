#!/usr/bin/env python3
"""Scenario 2 — Discovery.

Fact-checkers find work through the pending queue, with no assignment or
locking. A claim leaves an org's queue once that org has checked it, and the
consortium coordinates only through the ledger rejecting late submissions.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *  # noqa: E402,F403

title("Scenario 2 — Discovery: the pending queue")

root = new_claim()

visible = [c for c in pending("org2") if c.get("root_cid") == root]
check(visible, "a new claim appears in an org's queue", f"{len(pending('org2'))} open")
check(visible and "content" in visible[0],
      "queue entries carry readable content, not just a hash",
      "content present" if visible and "content" in visible[0] else "MISSING")

check(any(c.get("root_cid") == root for c in pending("org3")),
      "the same claim is visible to every org", "no assignment or locking")

fact_check("org2", root, "1")
check(not any(c.get("root_cid") == root for c in pending("org2")),
      "leaves org2's queue once org2 has checked it", "filtered out")
check(any(c.get("root_cid") == root for c in pending("org3")),
      "but stays in org3's queue", "still open to others")

statuses = {c.get("status") for c in pending("org3")}
check(statuses <= {"PENDING", "UNDER_REVIEW", "REOPENED"},
      "queue contains only open statuses", ",".join(sorted(statuses)) or "empty")

summary()
