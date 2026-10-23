"""Generic IPFS read/write bridge — a "dumb pipe" over kubo's HTTP API,
mirroring how apps/fabric_gateway is a dumb pipe over the Fabric SDK: it
knows nothing about report schemas, verdicts, or any other application
concept, just raw bytes in (POST /add -> CID) and raw bytes out
(GET /cat/{cid} -> bytes). blockchain_gateway is the only thing that knows
what those bytes mean.

Also independently reachable — downstream consumers that just need to fetch
a report by CID (a public-transparency dashboard, an archival tool, another
org's own systems) can hit this directly without going through
blockchain_gateway's org-authenticated API at all, since IPFS content is
already content-addressed: knowing the CID is itself the access grant, the
same trust model any public IPFS gateway uses."""
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response

from .config import settings

app = FastAPI(title="IPFS Gateway")

# CIDv0 (base58btc, Qm...) or CIDv1 (multibase base32 b... / base36 k...).
# Anything else is refused before it is spliced into kubo's ?arg= query.
CID_RE = re.compile(r"^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{20,100}|k[0-9a-z]{20,100})$")


def _token_ok(presented: Optional[str]) -> bool:
    if not settings.internal_api_token or presented is None:
        return False
    digest = lambda v: hashlib.sha256(v.encode("utf-8")).digest()  # noqa: E731
    return hmac.compare_digest(digest(presented), digest(settings.internal_api_token))


class IpfsError(Exception):
    pass


def _request(endpoint: str, timeout: int, data: Optional[bytes] = None) -> dict:
    req = urllib.request.Request(f"{settings.ipfs_api_url}/{endpoint}", data=data or b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise IpfsError(f"IPFS {endpoint} failed: {exc}") from exc


@app.get("/health")
def health():
    try:
        resp = _request("version", timeout=3)
        return {"status": "ok", "ipfs_available": bool(resp.get("Version")), "ipfs_version": resp.get("Version")}
    except IpfsError:
        return {"status": "ok", "ipfs_available": False}


@app.post("/add")
async def add(request: Request, x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token")):
    """Store raw request-body bytes in IPFS, return its CID."""
    if not _token_ok(x_internal_token):
        raise HTTPException(status_code=401, detail="missing or invalid X-Internal-Token")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_add_bytes:
        raise HTTPException(status_code=413, detail="blob too large")
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty body")
    if len(data) > settings.max_add_bytes:
        raise HTTPException(status_code=413, detail="blob too large")
    boundary = "----ipfsgw" + str(time.time_ns())
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="blob"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{settings.ipfs_api_url}/add",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"IPFS add failed: {exc}")
    cid = resp.get("Hash")
    if not cid:
        raise HTTPException(status_code=502, detail=f"IPFS add returned no CID: {resp}")
    return {"cid": cid}


@app.get("/cat/{cid}")
def cat(cid: str):
    """Fetch raw bytes for a CID, exactly as stored — no reinterpretation."""
    if not CID_RE.fullmatch(cid):
        raise HTTPException(status_code=400, detail="malformed CID")
    req = urllib.request.Request(f"{settings.ipfs_api_url}/cat?arg={cid}", data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 500:
            raise HTTPException(status_code=404, detail=f"no content found for CID {cid}")
        raise HTTPException(status_code=502, detail=f"IPFS cat failed: {exc}")
    except (urllib.error.URLError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"IPFS cat failed: {exc}")
    return Response(content=data, media_type="application/octet-stream")
