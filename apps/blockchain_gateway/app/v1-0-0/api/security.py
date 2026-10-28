from __future__ import annotations

import collections
import os
import re
import threading
import time
from typing import Deque, Dict, Iterable, List
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# IPFS CIDs (v0 base58 / v1 base32), legacy uuid hex ids and the
# caliper-/stress- ids benchmarks write straight to the ledger.
REPORT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MSP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ORG_RE = re.compile(r"^org[0-9]{1,3}$")
MSG_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(256 * 1024)))
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "0") == "1"


def validate_report_id(report_id: str) -> str:
    if not REPORT_ID_RE.fullmatch(report_id):
        raise HTTPException(status_code=400, detail="malformed report id")
    return report_id


def validate_msp(msp: str) -> str:
    if not MSP_RE.fullmatch(msp):
        raise HTTPException(status_code=400, detail="malformed MSP id")
    return msp


def is_safe_url(url: str) -> bool:
    # Evidence links are rendered as <a href> in the web client, so only absolute http(s) URLs without embedded credentials are accepted — javascript:, data: and friends never reach a browser.
    if len(url) > 2048 or any(c.isspace() for c in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.username


def check_urls(urls: Iterable[str]) -> List[str]:
    urls = list(urls)
    bad = [u for u in urls if not is_safe_url(u)]
    if bad:
        raise ValueError(f"evidence links must be absolute http(s) URLs: {bad[0][:80]!r}")
    return urls


def client_ip(request: Request) -> str:
    if TRUST_PROXY_HEADERS:
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


class SlidingWindowLimiter:
    """At most `limit` hits per `window_s` per key, kept in memory. One
    gateway process is the deployment unit here; a multi-replica deployment
    would move this into Redis or the edge proxy."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: Dict[str, Deque[float]] = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:  # drop idle keys so memory stays bounded
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


_API_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


def install_security_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _harden(request: Request, call_next):
        length = request.headers.get("content-length")
        if length is not None:
            try:
                too_big = int(length) > MAX_BODY_BYTES
            except ValueError:
                return JSONResponse({"detail": "invalid Content-Length"}, status_code=400)
            if too_big:
                return JSONResponse({"detail": "request body too large"}, status_code=413)
        response = await call_next(request)
        if request.url.path.startswith("/api/") or request.url.path == "/healthz":
            for name, value in _API_HEADERS.items():
                response.headers.setdefault(name, value)
        return response
