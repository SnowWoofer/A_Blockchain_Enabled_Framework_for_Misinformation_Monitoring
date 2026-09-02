#!/usr/bin/env python3
from __future__ import annotations
import argparse
import collections
import json
import signal
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


class TokenBucket:
    def __init__(self, rate: float) -> None:
        self.rate = float(rate)
        self._lock = threading.Lock()
        self._tokens = 0.0
        self._ts = time.perf_counter()

    def take(self, stop: threading.Event, deadline: float | None) -> bool:
        while True:
            if stop.is_set():
                return False
            if deadline is not None and time.perf_counter() >= deadline:
                return False
            with self._lock:
                now = time.perf_counter()
                self._tokens = min(self.rate, self._tokens + (now - self._ts) * self.rate)
                self._ts = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                wait = max(0.005, min(0.05, (1.0 - self._tokens) / max(self.rate, 0.001)))
            time.sleep(wait)


def worker(
    worker_id: int,
    base: str,
    api_key: str,
    rw_mix: int,
    samples: int,
    results: list,
    rate: float,
    bucket: TokenBucket | None = None,
    stop: threading.Event | None = None,
    deadline: float | None = None,
    tag: str = "stress",
) -> None:
    inter = 1.0 / rate if rate > 0 else 0.0
    i = 0
    while True:
        if bucket is not None:
            if not bucket.take(stop, deadline):
                return
        else:
            if stop is not None and stop.is_set():
                return
            if deadline is not None and time.perf_counter() >= deadline:
                return
            if i >= samples:
                return
        if rw_mix >= 100 or (i % 100) < rw_mix:
            body = {
                "msg_id": f"{tag}-{worker_id}-{i}",
                "label": "1",
                "confidence": 0.9,
                "model_version": "stress-v1",
                "content": "stress test payload",
                "source_platform": "loadtest",
            }
            status, ms = _post(base, "/api/reports", body, api_key)
        else:
            status, ms = _get(base, "/api/reports", api_key)
        results.append((status, ms))
        i += 1
        if bucket is None and inter > 0:
            time.sleep(inter)


def progress_loop(results: list, stop: threading.Event, started: float, interval: float = 10.0) -> None:
    while not stop.wait(interval):
        n = len(results)
        el = time.perf_counter() - started
        if el > 0:
            print(f"[progress] {el:8.1f}s | {n:7d} reqs | {n / el:7.2f} req/s", flush=True)


def summarize(results: list, elapsed: float) -> int:
    lat = sorted(ms for _, ms in results)
    ok = sum(1 for s, _ in results if s and s < 400)
    codes = collections.Counter(s for s, _ in results)

    def pct(p: float) -> float:
        if not lat:
            return 0.0
        return lat[min(len(lat) - 1, int(len(lat) * p))]

    print("=" * 64)
    print(f" requests   : {len(results)}  ({ok} ok / {len(results) - ok} failed)")
    print(f" elapsed    : {elapsed:.1f}s")
    if results and elapsed > 0:
        print(f" throughput : {len(results) / elapsed:.2f} req/s")
    if lat:
        print(f" latency avg: {statistics.fmean(lat):.1f} ms")
    print(
        f" p50/p95/p99/max: {pct(0.50):.1f} / {pct(0.95):.1f} / "
        f"{pct(0.99):.1f} / {(lat[-1] if lat else 0.0):.1f} ms"
    )
    ordered = dict(sorted(codes.items(), key=lambda kv: (kv[0] == 0, kv[0])))
    print(f" status     : {ordered}")
    print("=" * 64)
    return 0 if results else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="HTTP load driver")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--concurrency", type=int, default=24)
    ap.add_argument("--rate", type=float, default=25.0,
                    help="legacy per-worker pacing used in fixed-sample mode")
    ap.add_argument("--rw-mix", type=int, default=100, help="%% of requests that are writes")
    ap.add_argument("--samples", type=int, default=200, help="total requests in fixed mode")
    ap.add_argument("--rps", type=float, default=None,
                    help="stream mode: global target rate (token-bucket paced)")
    ap.add_argument("--duration", type=float, default=None,
                    help="stream mode: seconds to run")
    ap.add_argument("--forever", action="store_true",
                    help="stream mode: run until Ctrl-C")
    ap.add_argument("--label", default="stress", help="report_id prefix")
    args = ap.parse_args()

    api_key = "stress-key"
    tag = f"{args.label}-{int(time.time())}"
    results: list = []
    stop = threading.Event()
    bucket: TokenBucket | None = None
    deadline: float | None = None

    stream = args.forever or args.duration is not None or args.rps is not None
    if stream:
        pace = args.rps if args.rps is not None else args.rate
        mode_end = "until Ctrl-C" if args.forever else f"for {args.duration:g}s"
        print(f"[mode] stream | base={args.base} | {pace:g} req/s global | {mode_end}")
        print(f"[mode] report ids look like {tag}-<worker>-<seq>")
        bucket = TokenBucket(pace)
    else:
        print(f"[mode] fixed samples | base={args.base} | total~{args.samples}")

    def request_stop(signum: int, frame: object) -> None:
        if not stop.is_set():
            print("\n[signal] Ctrl-C — finishing in-flight requests...", flush=True)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)

    started = time.perf_counter()
    if args.duration is not None:
        deadline = started + args.duration

    threads = []
    per_worker = max(1, args.samples // max(1, args.concurrency))
    for w in range(max(1, args.concurrency)):
        t = threading.Thread(
            target=worker, daemon=True,
            args=(w, args.base, api_key, args.rw_mix, per_worker, results,
                  args.rate, bucket, stop, deadline, tag),
        )
        threads.append(t)

    if bucket is not None:
        threading.Thread(target=progress_loop, args=(results, stop, started), daemon=True).start()

    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop.set()
    elapsed = time.perf_counter() - started
    return summarize(results, elapsed)


if __name__ == "__main__":
    sys.exit(main())
