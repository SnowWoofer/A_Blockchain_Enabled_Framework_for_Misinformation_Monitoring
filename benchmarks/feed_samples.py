#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

def _post(base: str, path: str, body: dict, api_key: str = "",
          timeout: float = 30.0) -> tuple[int, dict | str, float]:
    start = time.perf_counter()
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(base + path, data=data, method="POST",
                                headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ms = (time.perf_counter() - start) * 1000.0
            try:
                return r.status, json.loads(raw), ms
            except Exception:
                return r.status, raw.decode("utf-8", errors="replace"), ms
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        ms = (time.perf_counter() - start) * 1000.0
        try:
            return exc.code, json.loads(raw), ms
        except Exception:
            return exc.code, raw.decode("utf-8", errors="replace"), ms
    except (urllib.error.URLError, OSError) as exc:
        ms = (time.perf_counter() - start) * 1000.0
        return 0, str(exc), ms

def _get(base: str, path: str, api_key: str = "",
         timeout: float = 30.0) -> tuple[int, dict | str, float]:
    start = time.perf_counter()
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ms = (time.perf_counter() - start) * 1000.0
            try:
                return r.status, json.loads(raw), ms
            except Exception:
                return r.status, raw.decode("utf-8", errors="replace"), ms
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        ms = (time.perf_counter() - start) * 1000.0
        try:
            return exc.code, json.loads(raw), ms
        except Exception:
            return exc.code, raw.decode("utf-8", errors="replace"), ms
    except (urllib.error.URLError, OSError) as exc:
        ms = (time.perf_counter() - start) * 1000.0
        return 0, str(exc), ms

def _generate_keys(num_orgs: int, mode: str) -> list[str]:
    return [f"key-org{i}" for i in range(1, num_orgs + 1)]

def _org_for_key(key: str, num_orgs: int, mode: str) -> str:
    for i in range(1, num_orgs + 1):
        if key == f"key-org{i}":
            return f"org{i}"
    return "unknown"

def _load_samples(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        print(f"ERROR: expected a JSON array in {path}", file=sys.stderr)
        sys.exit(1)
    return data

def _predict(base_url: str, text: str) -> dict | None:
    status, body, _ = _post(base_url, "/predict", {"post_text": text})
    if status == 200 and isinstance(body, dict):
        return body
    print(f"  WARNING: /predict returned {status}: {body}", file=sys.stderr)
    return None

def _submit_report(base_url: str, api_key: str, msg_id: str, label: str,
                   confidence: float, content: str,
                   model_version: str = "afro-xlmr-large-76L-misinfo-v1",
                   source_platform: str = "ai_scout") -> tuple[str | None, float]:
    body = {
        "msg_id": msg_id,
        "label": label,
        "confidence": round(confidence, 6),
        "model_version": model_version,
        "content": content,
        "source_platform": source_platform,
    }
    status, resp, ms = _post(base_url, "/api/reports", body, api_key=api_key)
    if status == 200 and isinstance(resp, dict):
        return resp.get("id"), ms
    print(f"  WARNING: /api/reports returned {status}: {resp}", file=sys.stderr)
    return None, ms

def _fact_check(base_url: str, api_key: str, report_id: str,
                outcome: str) -> bool:
    body = {
        "outcome": outcome,
        "reasoning": f"Automated fact-check: outcome={outcome}",
        "support": [],
    }
    status, _, _ = _post(base_url, f"/api/reports/{report_id}/fact-check",
                         body, api_key=api_key)
    return status == 200

def _get_report_status(base_url: str, api_key: str,
                       report_id: str) -> str | None:
    status, body, _ = _get(base_url, f"/api/reports/{report_id}/chain",
                           api_key=api_key)
    if status == 200 and isinstance(body, dict):
        return body.get("status")
    return None

def _detect_org_count(base_url: str, api_key: str) -> int | None:
    """Count orgs registered on-chain via GET /api/orgs, so the harness
    mirrors the actual consortium instead of trusting a manual --num-orgs
    (the chain's quorum math depends on the real registered count)."""
    status, body, _ = _get(base_url, "/api/orgs", api_key=api_key)
    if status == 200 and isinstance(body, list):
        return len(body)
    return None

def _decide_voting_outcomes(num_orgs: int, v: int, reject: bool,
                            rng: random.Random) -> list[str]:
    # Mirrors the chaincode's consensus rule (SubmitFactCheck)
    required = math.ceil(2.0 / 3.0 * num_orgs)
    remaining = num_orgs - v
    if reject:
        # Reject
        yes = max(0, required - remaining - 1)
        votes = ["1"] * yes + ["0"] * (v - yes)
    else:
        # Accept
        yes = min(v, required)
        votes = ["1"] * yes + ["0"] * (v - yes)
    rng.shuffle(votes)
    return votes

def _write_csv(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "timestamp", 
        "sample_id", 
        "statement", 
        "ground_truth", 
        "mode",
        "model_label", 
        "model_confidence", 
        "submitted_label", 
        "org",
        "report_id", 
        "accepted", 
        "votes_cast", 
        "final_status", 
        "latency_ms",
    ]
    write_header = not path.exists() or path.stat().st_size == 0
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for r in results:
            row = {k: r.get(k) for k in fieldnames if k != "timestamp"}
            row["timestamp"] = ts
            writer.writerow(row)

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Feed samples through pipeline")
    ap.add_argument("--data", default="data/translated_masked_samples_test.json",
                    help="path samples file")
    ap.add_argument("--samples", type=int, default=0,
                    help="randomly select N samples")
    ap.add_argument("--ai-pct", type=int, default=100,
                    help="%% of samples processed by the AI model, 0 -> 100")
    ap.add_argument("--num-orgs", type=int, default=None,
                    help="override on-chain org count (default: auto-detected "
                         "from /api/orgs; generate keys key-org1..key-orgN)")
    ap.add_argument("--mode", choices=["direct", "indirect"], default="direct",
                    help="direct == org1 is the AI stakeholder: it submits ai_pct%% "
                         "of reports (the AI-processed ones), random org2..orgN "
                         "submit the rest; everyone except the submitter votes. "
                         "indirect == random org submits each report")
    ap.add_argument("--reject-pct", type=int, default=0,
                    help="%% of reports with non 2/3 consensus (rejected)")
    ap.add_argument("--fact-check", dest="fact_check", action="store_true", default=True,
                    help="simulate fact-checking after submission (default: on)")
    ap.add_argument("--no-fact-check", dest="fact_check", action="store_false",
                    help="disable fact-checking")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="parallel workers (currently 1 only)")
    ap.add_argument("--rate", type=float, default=0.0,
                    help="requests per second (0 = unlimited)")
    ap.add_argument("--endpoint", default="http://localhost:8004",
                    help="flagging-engine /predict URL")
    ap.add_argument("--blockchain-api", default="http://localhost:8000",
                    help="blockchain_gateway base URL")
    ap.add_argument("--seed", type=int, default=100,
                    help="random seed for reproducibility (default: 100)")
    default_output = os.path.join("results", "local", "real_loads.csv")
    ap.add_argument("--output", default=default_output,
                    help="output path (.csv for CSV, .jsonl for JSONL)")
    args = ap.parse_args()

    # The chain's consensus math
    num_orgs = args.num_orgs
    for probe in ("key-org1", "stress-key"):
        detected = _detect_org_count(args.blockchain_api, probe)
        if detected:
            num_orgs = detected
            break
    if not num_orgs:
        print(f"ERROR: could not determine on-chain org count via "
              f"{args.blockchain_api}/api/orgs (tried key-org1, stress-key). "
              f"Pass --num-orgs N explicitly.", file=sys.stderr)
        return 2
    if args.num_orgs and args.num_orgs != num_orgs:
        print(f"NOTE: --num-orgs {args.num_orgs} overridden by detected "
              f"on-chain org count {num_orgs} (consortium size drives quorum).",
              file=sys.stderr)

    if args.mode == "direct" and num_orgs < 3:
        print(f"ERROR: direct mode requires at least 3 orgs "
              f"(org1 is the AI stakeholder; org2..orgN handle the rest). "
              f"Got {num_orgs}.",
              file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    org_keys = _generate_keys(num_orgs, args.mode)
    # org1 is the AI stakeholder org: it submits ai_pct% of reports (the ones
    # the AI processed) and votes on reports it did not submit. voting_org_keys
    # below is the full pool; the per-report submitter is excluded at
    # fact-check time by the existing `available_keys` filter.
    submitting_org_key = "key-org1"
    voting_org_keys = list(org_keys)

    samples = _load_samples(args.data)
    n = args.samples if args.samples > 0 else len(samples)
    n = min(n, len(samples))
    selected = rng.sample(samples, n)

    ai_count = max(0, min(n, round(n * args.ai_pct / 100.0)))
    reject_count = max(0, min(n, round(n * args.reject_pct / 100.0)))

    ai_indices = set(range(ai_count))
    reject_indices = set(range(reject_count))

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    results = []
    submitted = 0
    accepted = 0
    rejected = 0
    latencies = []
    org_counts: dict[str, int] = {}
    total_fc_yes = 0
    total_fc_no = 0

    print(f"Samples: {n} | AI: {ai_count} | Random: {n - ai_count} | "
          f"Reject-pct: {args.reject_pct}% ({reject_count} reports) | "
          f"Orgs: {num_orgs} (on-chain) | Mode: {args.mode}")
    print()

    for i, sample in enumerate(selected):
        statement = sample.get("statement", "")
        ground_truth = sample.get("classification", "unknown")
        sample_id = sample.get("id", f"sample-{i}")
        msg_id = f"feed_{uuid.uuid4().hex[:12]}"

        # prediction
        if i in ai_indices:
            pred = _predict(args.endpoint, statement)
            if pred:
                label = "1" if pred.get("flagged") else "0"
                confidence = pred.get("confidence", pred.get(
                    "misinformation_probability", 0.5))
                model_label = pred.get("label", "unknown")
            else:
                label = "1" if rng.random() < 0.5 else "0"
                confidence = rng.uniform(0.5, 1.0)
                model_label = "fallback_random"
        else:
            label = "1" if rng.random() < 0.5 else "0"
            confidence = rng.uniform(0.5, 1.0)
            model_label = "random"

        # choose submitter org
        if args.mode == "direct":
            # AI stakeholder: the AI submits the reports it processed (ai_pct%);
            # the rest go out from a random non-AI org. All orgs are equal.
            if i in ai_indices:
                submit_key = submitting_org_key
            else:
                submit_key = rng.choice(org_keys[1:])
        else:
            submit_key = rng.choice(voting_org_keys)
        submit_org = _org_for_key(submit_key, num_orgs, args.mode)

        # submit report
        report_id, ms = _submit_report(
            args.blockchain_api, submit_key, msg_id, label, confidence,
            statement)
        latencies.append(ms)

        if report_id:
            submitted += 1
            org_counts[submit_org] = org_counts.get(submit_org, 0) + 1

            # fact-checking
            fc_outcomes: list[dict] = []
            final_status = "SUBMITTED"
            if args.fact_check:
                is_rejected = (i in reject_indices)
                available_keys = [k for k in voting_org_keys
                                  if k != submit_key]
                rng.shuffle(available_keys)
                votes = _decide_voting_outcomes(
                    num_orgs, len(available_keys), is_rejected, rng)
                vote_keys = available_keys[:len(votes)]

                for vk, outcome in zip(vote_keys, votes):
                    ok = _fact_check(args.blockchain_api, vk, report_id,
                                    outcome)
                    if not ok:
                        break
                    fc_org = _org_for_key(vk, num_orgs, args.mode)
                    fc_outcomes.append({"org": fc_org, "outcome": outcome})
                    if outcome == "1":
                        total_fc_yes += 1
                    else:
                        total_fc_no += 1

                    status = _get_report_status(args.blockchain_api,
                                                voting_org_keys[0],
                                                report_id)
                    if status in ("FINAL", "REJECTED"):
                        final_status = status
                        break

                if final_status == "SUBMITTED":
                    final_status = _get_report_status(
                        args.blockchain_api, voting_org_keys[0],
                        report_id) or "UNKNOWN"

            if final_status == "FINAL":
                accepted += 1
            elif final_status == "REJECTED":
                rejected += 1
        else:
            final_status = "SUBMIT_FAILED"
            fc_outcomes = []

        results.append({
            "sample_id": sample_id,
            "statement": statement,
            "ground_truth": ground_truth,
            "mode": args.mode,
            "model_label": model_label,
            "model_confidence": round(confidence, 4),
            "submitted_label": label,
            "org": submit_org,
            "report_id": report_id,
            "accepted": final_status == "FINAL",
            "fact_checks": fc_outcomes,
            "votes_cast": len(fc_outcomes),
            "final_status": final_status,
            "latency_ms": round(ms, 1),
        })

        tag = "OK" if final_status == "FINAL" else (
            "REJ" if final_status == "REJECTED" else "FAIL")
        print(f"  [{i+1:4d}/{n}] {tag} {sample_id[:20]:20s}  "
              f"org={submit_org:8s}  label={label}  "
              f"conf={confidence:.2f}  fc={len(fc_outcomes)}  "
              f"{ms:.0f}ms")

    # write output 
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix == ".csv":
        _write_csv(out_path, results)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # summary
    print()
    print("=" * 64)
    print(f"  total samples:    {n}")
    print(f"  AI-processed:     {ai_count}  ({args.ai_pct}%)")
    print(f"  random-pool:      {n - ai_count}  ({100 - args.ai_pct}%)")
    print(f"  submitted:        {submitted}")
    print(f"  accepted:         {accepted}  ({100*accepted/max(submitted,1):.1f}%)")
    print(f"  rejected:         {rejected}  ({100*rejected/max(submitted,1):.1f}%)")
    print()
    print("  by org:")
    for org, count in sorted(org_counts.items(), key=lambda x: -x[1]):
        print(f"    {org:12s}  {count} submitted")
    print()
    if args.fact_check:
        print(f"  fact-checks:")
        print(f"    YES votes:  {total_fc_yes}")
        print(f"    NO votes:   {total_fc_no}")
    print()
    if latencies:
        valid = [l for l in latencies if l > 0]
        if valid:
            print(f"  avg latency:  {statistics.fmean(valid):.1f}ms")
            sl = sorted(valid)
            p50 = sl[len(sl)//2]
            p95 = sl[int(len(sl)*0.95)]
            print(f"  p50/p95:      {p50:.1f} / {p95:.1f}ms")
    print("=" * 64)
    print(f"  Output: {args.output}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
