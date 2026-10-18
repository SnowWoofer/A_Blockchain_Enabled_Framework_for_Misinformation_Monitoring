"""Authenticated internal bridge to a managed private Kubo node.

Possession of a CID does not grant access through this service. The caller
needs the provisioned service credential; end users use the workflow API.
Private swarm membership protects transport, not encryption at rest.
"""
import json
import hmac
import urllib.parse
import time
import urllib.error
import urllib.request
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response

from .config import settings

app = FastAPI(title="IPFS Gateway")


@app.middleware('http')
async def authenticate(request: Request, call_next):
    if request.url.path != '/health':
        if not settings.service_key:
            return Response('storage service key is not configured', status_code=503)
        if not hmac.compare_digest(request.headers.get('X-Service-Key',''), settings.service_key):
            return Response('invalid service credential', status_code=401)
    return await call_next(request)


class IpfsError(Exception):
    pass


def _request(endpoint: str, timeout: int, data: Optional[bytes] = None) -> dict:
    req = urllib.request.Request(f"{settings.ipfs_api_url}/{endpoint}", data=data or b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail=exc.read(2000).decode(errors='replace')
        raise IpfsError(f'IPFS operation rejected ({exc.code}): {detail}') from exc
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
async def add(request: Request):
    """Store raw request-body bytes in IPFS, return its CID."""
    data = await request.body()
    if len(data) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='object exceeds 4 MiB')
    if not data:
        raise HTTPException(status_code=400, detail="empty body")
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
    req = urllib.request.Request(f"{settings.ipfs_api_url}/cat?arg={urllib.parse.quote(cid, safe='')}", data=b"", method="POST")
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


@app.post('/admin/unpin/{cid}')
def unpin(cid: str):
    try:
        return _request('pin/rm?arg='+urllib.parse.quote(cid,safe=''),timeout=15)
    except IpfsError as exc:
        if 'not pinned' in str(exc):return {'already_unpinned':True}
        raise HTTPException(502,'unpin failed') from exc


@app.post('/admin/gc')
def garbage_collect():
    # Kubo streams newline-delimited JSON; do not parse it as one JSON object.
    req=urllib.request.Request(f'{settings.ipfs_api_url}/repo/gc',data=b'',method='POST')
    try:
        with urllib.request.urlopen(req,timeout=60) as response:
            result=[json.loads(line) for line in response if line.strip()]
        if any(row.get('Error') for row in result):raise IpfsError('garbage collector reported an error')
        return {'results':result}
    except (urllib.error.URLError,OSError,ValueError,IpfsError) as exc:
        raise HTTPException(502,'garbage collection failed') from exc
