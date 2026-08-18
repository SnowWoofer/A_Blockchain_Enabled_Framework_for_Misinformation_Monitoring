#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request


def _post(base: str, path: str, body: dict, api_key: str) -> tuple[int, float]:
    start = time.perf_counter()
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
            return r.status, (time.perf_counter() - start) * 1000.0
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code, (time.perf_counter() - start) * 1000.0
    except (urllib.error.URLError, OSError) as exc:
        return 0, (time.perf_counter() - start) * 1000.0


def _get(base: str, path: str, api_key: str) -> tuple[int, float]:
    start = time.perf_counter()
    req = urllib.request.Request(base + path, headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
            return r.status, (time.perf_counter() - start) * 1000.0
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code, (time.perf_counter() - start) * 1000.0
    except (urllib.error.URLError, OSError) as exc:
        return 0, (time.perf_counter() - start) * 1000.0


def worker(
    worker_id: int,
    base: str,
    api_key: str,
    rw_mix: int,
    samples: int,
    results: list,
    rate: float,
) -> None:
    inter = 1.0 / rate if rate > 0 else 0.0
    for i in range(samples):
        if rw_mix >= 100 or (i % 100) < rw_mix:
            body = {
                "report_id": f"stress-{worker_id}-{i}",
                "language": "nso",
                "label": "1",
                "confidence": 0.9,
                "model_version": "stress-v1",
                "raw_text": "stress test payload",
            }
            status, ms = _post(base, "/api/reports", body, api_key)
        else:
            status, ms = _get(base, "/api/reports", api_key)
        results.append((status, ms))
        if inter > 0:
            time.sleep(inter)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--concurrency", type=int, default=24)
    ap.add_argument("--rate", type=float, default=25.0)
    ap.add_argument("--rw-mix", type=int, default=100)
    ap.add_argument("--samples", type=int, default=200)
    args = ap.parse_args()
    api_key = "stress-key"
    results: list = []
    threads = []
    per_worker = max(1, args.samples // args.concurrency)
    for w in range(args.concurrency):
        t = threading.Thread(
            target=worker,
            args=(w, args.base, api_key, args.rw_mix, per_worker, results, args.rate),
        )
        threads.append(t)
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start
    ok = [r for r in results if r[0] and r[0] < 400]
    fail = [r for r in results if not (r[0] and r[0] < 400)]
    lat = sorted(r[1] for r in results)

    def pct(p: float) -> float:
        if not lat:
            return 0.0
        idx = min(len(lat) - 1, int(len(lat) * p))
        return lat[idx]
    return 0

if __name__ == "__main__":
    sys.exit(main())
