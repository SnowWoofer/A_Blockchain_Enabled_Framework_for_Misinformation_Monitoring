#!/usr/bin/env python3
# Populate a demo deployment (demo.sh, FABRIC_BACKEND=mock) with sample claims and votes so the web client has something to show.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FALLBACK_CLAIMS = [
    ("Voting stations in Polokwane will close at noon on election day.", "misinformation"),
    ("The electoral commission has extended voter registration by one week.", "non-misinformation"),
    ("Drinking hot water with lemon cures cholera within a day.", "misinformation"),
    ("Load shedding stage 6 has been announced for the whole of Gauteng tonight.", "misinformation"),
    ("The national health department recommends washing hands with soap for 20 seconds.", "non-misinformation"),
    ("A new law lets police confiscate cellphones at taxi ranks without a warrant.", "misinformation"),
]
DEMO_REASON = ("[demo data] Seeded by demo-seed.py to illustrate the review workflow; "
               "no real fact-check was carried out for this vote.")


def post(base: str, path: str, body: dict, key: str) -> dict:
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{path}: HTTP {exc.code} {exc.read().decode(errors='replace')}") from exc


def load_claims(n: int, rng: random.Random) -> list[tuple[str, str]]:
    data = ROOT / "data" / "test.json"
    if data.exists():
        rows = [r for r in json.loads(data.read_text()) if r.get("statement")]
        rng.shuffle(rows)
        return [(r["statement"], r.get("classification", "")) for r in rows[:n]]
    return (FALLBACK_CLAIMS * (n // len(FALLBACK_CLAIMS) + 1))[:n]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gateway", default="http://127.0.0.1:8000")
    ap.add_argument("--db", default=str(ROOT / "apps/blockchain_gateway/app/v1-0-0/api/offchain.db"))
    ap.add_argument("--orgs", type=int, default=3)
    ap.add_argument("--claims", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    submitter = os.environ.get("SUBMITTER_API_KEY")
    if not submitter:
        print("SUBMITTER_API_KEY is not set (source the repo-root .env)", file=sys.stderr)
        return 2
    if args.orgs < 3:
        print("--orgs must be at least 3 (demo.sh's smallest consortium)", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    voters = [f"org{i}" for i in range(2, args.orgs + 1)]
    temp = {org: secrets.token_urlsafe(24) for org in voters}
    db = sqlite3.connect(args.db)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db:
        for org, key in temp.items():
            db.execute("INSERT OR REPLACE INTO api_keys (key_hash, org, role, label, created_at, revoked) "
                       "VALUES (?, ?, 'official', 'demo-seed', ?, 0)",
                       (hashlib.sha256(key.encode()).hexdigest(), org, now))
    required = (2 * args.orgs + 2) // 3
    try:
        for i, (text, truth) in enumerate(load_claims(args.claims, rng)):
            ai_says = "1" if truth == "misinformation" else "0"
            if rng.random() < 0.15:  # the AI is not always right
                ai_says = "1" if ai_says == "0" else "0"
            report = post(args.gateway, "/api/reports", {
                "msg_id": f"demo_{i}_{secrets.token_hex(4)}", "label": ai_says,
                "confidence": round(rng.uniform(0.62, 0.98), 4), "model_version": "demo-seed",
                "content": text, "source_platform": rng.choice(["ai_scout", "public_tip"]),
            }, submitter)
            plan = i % 4  # 0: confirmed, 1: not confirmed, 2: partly voted, 3: untouched
            if plan == 0:
                outcomes = ["1"] * required
            elif plan == 1:
                outcomes = ["0"]
            elif plan == 2:
                outcomes = ["1"] if required > 1 else []
            else:
                outcomes = []
            for org, outcome in zip(voters, outcomes):
                post(args.gateway, f"/api/reports/{report['id']}/fact-check",
                     {"outcome": outcome, "reasoning": DEMO_REASON, "support": []}, temp[org])
            print(f"  claim {i + 1:2}: {['confirmed', 'not confirmed', 'partly voted', 'awaiting votes'][plan]}")
    finally:
        with db:
            db.execute("UPDATE api_keys SET revoked=1 WHERE label='demo-seed'")
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
