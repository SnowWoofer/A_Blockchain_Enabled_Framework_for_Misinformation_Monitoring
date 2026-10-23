from __future__ import annotations
import os
import sys
from pathlib import Path
from unittest import mock

os.environ["INTERNAL_API_TOKEN"] = "test-token"
os.environ["IPFS_API_URL"] = "http://127.0.0.1:1/api/v0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

client = TestClient(main.app)
CID = "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"


class _Resp:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_add_requires_token():
    assert client.post("/add", content=b"x").status_code == 401
    assert client.post("/add", content=b"x", headers={"X-Internal-Token": "wrong"}).status_code == 401


def test_add_with_token_reaches_kubo():
    with mock.patch.object(main.urllib.request, "urlopen", return_value=_Resp(b'{"Hash": "%s"}' % CID.encode())):
        resp = client.post("/add", content=b"hello", headers={"X-Internal-Token": "test-token"})
    assert resp.status_code == 200 and resp.json() == {"cid": CID}


def test_add_size_limit():
    big = b"x" * (main.settings.max_add_bytes + 1)
    resp = client.post("/add", content=big, headers={"X-Internal-Token": "test-token"})
    assert resp.status_code == 413


def test_cat_rejects_malformed_cid():
    # %26 decodes to '&', which would otherwise add extra kubo query arguments
    assert client.get("/cat/Qmabc%26offset=5").status_code == 400
    assert client.get("/cat/not-a-cid").status_code == 400


def test_cat_valid_cid():
    with mock.patch.object(main.urllib.request, "urlopen", return_value=_Resp(b"payload")) as urlopen:
        resp = client.get(f"/cat/{CID}")
    assert resp.status_code == 200 and resp.content == b"payload"
    assert urlopen.call_args[0][0].full_url.endswith(f"/cat?arg={CID}")
