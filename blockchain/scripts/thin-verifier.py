#!/usr/bin/env python3
"""Independent verifier for a fact-checking organisation that runs NO
infrastructure — no Fabric peer, no IPFS node, no database.

It answers one question: does the claim document I was handed match the hash
the consortium anchored on the ledger? That check is pure arithmetic, so it
does not require trusting whoever served either the document or the record.

    ./thin-verifier.py <report-id> [--api http://host:8000] [--key key-org3]
    ./thin-verifier.py <report-id> --document ./copy-i-was-sent.json

Exit status is 0 only if the document verifies.
"""
import argparse
import hashlib
import json
import sys
import urllib.request


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def core_content(doc: dict) -> dict:
    """The immutable part of a claim — exactly what the gateway hashes."""
    return {"source": doc.get("source"), "inference": doc.get("inference")}


def fetch(url: str, api_key: str):
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report_id")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--key", default="key-org3")
    ap.add_argument("--document", help="verify a local copy someone handed you, "
                                       "instead of the one the gateway serves")
    args = ap.parse_args()

    base = args.api.rstrip("/")
    if args.document:
        with open(args.document) as fh:
            document = json.load(fh)
    else:
        document = fetch(f"{base}/api/reports/{args.report_id}", args.key)
    ledger = fetch(f"{base}/api/reports/{args.report_id}/chain", args.key)

    anchored = ledger.get("content_hash", "")
    recomputed = hashlib.sha256(canonical(core_content(document)).encode("utf-8")).hexdigest()
    ok = anchored == recomputed and bool(anchored)

    print(f"report id        : {args.report_id}")
    print(f"claim            : {document.get('source', {}).get('content', '')[:60]}")
    print(f"model said       : inference_label={document.get('inference', {}).get('label')}")
    print(f"consortium said  : status={ledger.get('status')} final_label={ledger.get('final_label', '-')}")
    print(f"votes            : {[(v['checker_msp'], v['outcome']) for v in ledger.get('fact_checks', [])]}")
    print(f"anchored hash    : {anchored}")
    print(f"recomputed hash  : {recomputed}")
    print(f"VERDICT          : {'VERIFIED - document matches the ledger' if ok else 'TAMPERED - does NOT match'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
