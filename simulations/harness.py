"""Shared plumbing for the scenario simulations.

Each scenario file is self-contained: it creates its own claim and drives it to
whatever state it needs, so the scenarios can run in any order, in isolation, or
repeatedly. Nothing depends on a previous scenario having run.

Requires a live stack with at least five orgs:
    ./startup.sh --orgs 5 --skip-caliper
    docker compose up -d --build
"""
import json
import sys
import time
import urllib.error
import urllib.request

GATEWAY = "http://localhost:8000"
FACTCHECK = "http://localhost:8002"
INGEST = "http://localhost:8003"

_checks: list[tuple[bool, str, str]] = []


def call(method, base, path, key="key-org1", body=None, timeout=90):
    req = urllib.request.Request(
        base + path, method=method,
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None
    except urllib.error.URLError as e:
        raise SystemExit(f"cannot reach {base} — is the stack running?  ({e.reason})")


def chain(root_cid):
    return call("GET", GATEWAY, f"/api/reports/{root_cid}/chain")[1]


def document(root_cid):
    return call("GET", GATEWAY, f"/api/reports/{root_cid}")[1]


def history(root_cid):
    return call("GET", GATEWAY, f"/api/reports/{root_cid}/history")[1]


def verify(root_cid):
    return call("GET", GATEWAY, f"/api/reports/{root_cid}/verify")[1]


def fact_check(org, root_cid, outcome, reasoning=None):
    return call("POST", FACTCHECK, f"/claims/{root_cid}/fact-check", key=f"key-{org}",
                body={"outcome": outcome,
                      "reasoning": reasoning or f"{org} assessment",
                      "support": []})


def reopen(org, root_cid):
    return call("POST", FACTCHECK, f"/claims/{root_cid}/reopen", key=f"key-{org}")


def pending(org):
    return call("GET", FACTCHECK, "/claims/pending", key=f"key-{org}")[1] or []


DEFAULT_CLAIM = ("Observers reported that sealed ballot boxes were moved from the "
                 "counting hall before the official tally was announced.")


def new_claim(text=None, wait=120):
    """Ingest a claim and block until it is registered on-chain. Returns its root_cid.

    Each call produces a *new* claim, so scenarios never collide with each other
    or with a previous run.
    """
    st, r = call("POST", INGEST, "/ingest", body={"claims": [text or DEFAULT_CLAIM]})
    if st != 200:
        raise SystemExit(f"ingest failed: {st} {r}")
    ingest_id = r["ingest_ids"][0]
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(2)
        for rec in call("GET", GATEWAY, "/api/reports")[1] or []:
            doc = document(rec["root_cid"])
            if isinstance(doc, dict) and doc.get("ingest_id") == ingest_id:
                return rec["root_cid"]
    raise SystemExit(f"claim {ingest_id} never reached the ledger within {wait}s")


def drive_to_final(root_cid, outcomes=("1", "1", "0")):
    """Run round 0 to a verdict. Default is a 2-1 close on 'misinformation'."""
    for org, outcome in zip(("org1", "org2", "org3"), outcomes):
        fact_check(org, root_cid, outcome)
    return chain(root_cid)


def check(condition, label, detail=""):
    _checks.append((bool(condition), label, detail))
    mark = "\033[32mok\033[0m" if condition else "\033[31mFAIL\033[0m"
    print(f"  {label:<48} {detail:<26} {mark}")
    return bool(condition)


def title(text):
    print(f"\n\033[1m{text}\033[0m\n{'─' * min(len(text), 74)}")


def summary():
    """Print a tally and exit non-zero if anything failed."""
    failed = [c for c in _checks if not c[0]]
    print()
    if failed:
        print(f"\033[31m{len(failed)} of {len(_checks)} checks FAILED\033[0m")
        for _, label, detail in failed:
            print(f"  · {label} {detail}")
        sys.exit(1)
    print(f"\033[32mall {len(_checks)} checks passed\033[0m")
    sys.exit(0)
