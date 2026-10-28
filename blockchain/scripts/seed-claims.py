#!/usr/bin/env python3
"""Fill a fresh deployment's review queue with claims from data/test.json.

Picks --count claims from the multilingual evaluation set with a fixed --seed,
so every fresh deployment starts with the same claims, and sends them in
through claim-ingest-worker's POST /ingest (INGEST_API_KEY). From there they
take the real pipeline: Kafka -> flagging engine (the AI scores each one) ->
submission worker -> gateway -> IPFS + Fabric. Only the statement text is
sent; the dataset's ground-truth label stays out, so review stays blind.

Nothing is sent when the ledger already holds reports (--force to seed
anyway), so re-running the script does not duplicate the queue.

    python3 blockchain/scripts/seed-claims.py --count 10 --seed 42
"""
# Fill a fresh deployment's review queue with claims from data/test.json.
# Usage: python3 blockchain/scripts/seed-claims.py --count 10 --seed 42

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BATCH = 100  # claim-ingest-worker's max_claims_per_request


def request(url: str, body: dict | None = None, key: str = "") -> dict:
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{url}: HTTP {exc.code} {exc.read().decode(errors='replace')}") from exc


def ledger_reports(gateway: str) -> int:
    return int(request(f"{gateway}/api/public/stats").get("total_reports", 0))


def ingest_key() -> str:
    """INGEST_API_KEY from the environment, else from the repo-root .env."""
    if os.environ.get("INGEST_API_KEY"):
        return os.environ["INGEST_API_KEY"]
    env = ROOT / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        k, _, v = line.partition("=")
        if k.strip() == "INGEST_API_KEY":
            return v.strip()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=str(ROOT / "data" / "test.json"))
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42, help="fixed, so every deployment gets the same claims")
    ap.add_argument("--ingest", default="http://127.0.0.1:8003")
    ap.add_argument("--gateway", default="http://127.0.0.1:8000")
    ap.add_argument("--wait", type=int, default=900,
                    help="seconds to wait for the claims to reach the ledger (0: return once queued)")
    ap.add_argument("--force", action="store_true", help="seed even if the ledger already holds reports")
    args = ap.parse_args()

    data = Path(args.data)
    if not data.exists():
        print(f"   {data} not found (data/ is git-ignored): no claims seeded", file=sys.stderr)
        return 1
    key = ingest_key()
    if not key:
        print("   INGEST_API_KEY is not set (run blockchain/scripts/gen-secrets.sh)", file=sys.stderr)
        return 1
    rows = [r for r in json.loads(data.read_text()) if (r.get("statement") or "").strip()]
    picked = random.Random(args.seed).sample(rows, min(args.count, len(rows)))
    claims = [r["statement"] for r in picked]

    try:
        before = ledger_reports(args.gateway)
        if before and not args.force:
            print(f"   the ledger already holds {before} report(s): not seeding again (--force to seed anyway)")
            return 0
        for i in range(0, len(claims), BATCH):
            request(f"{args.ingest}/ingest", {"claims": claims[i:i + BATCH]}, key)
    except (RuntimeError, OSError) as exc:
        print(f"   seeding failed: {exc}", file=sys.stderr)
        return 1
    print(f"   queued {len(claims)} claims from {data.name} (seed {args.seed}) for AI screening")
    if args.wait <= 0:
        return 0

    deadline, arrived, shown = time.monotonic() + args.wait, 0, -1
    while time.monotonic() < deadline:
        try:
            arrived = ledger_reports(args.gateway) - before
        except (RuntimeError, OSError):
            pass
        if arrived >= len(claims):
            print(f"   all {len(claims)} claims are on the ledger, awaiting fact-checks")
            return 0
        if arrived != shown:
            print(f"   {arrived}/{len(claims)} on the ledger")
            shown = arrived
        time.sleep(10)
    print(f"   {arrived}/{len(claims)} on the ledger after {args.wait}s; the rest are still in the pipeline "
          "(docker logs flagging-engine / submission-worker)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
