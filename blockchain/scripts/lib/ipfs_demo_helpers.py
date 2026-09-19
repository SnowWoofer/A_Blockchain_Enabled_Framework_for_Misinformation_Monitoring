"""Helpers for ipfs-demo.sh — kept out of the shell script so neither language
has to be escaped inside the other."""
import json
import sys


def newest_report_id() -> None:
    reports = json.load(sys.stdin)
    reports.sort(key=lambda r: r["timestamp"])
    print(reports[-1]["id"] if reports else "")


def claim_text() -> None:
    try:
        print(json.load(sys.stdin)["source"]["content"][:52])
    except Exception:
        print("<retrieval failed>")


def tamper() -> None:
    """Rewrite the model's verdict to the opposite label, then re-serialise
    exactly the way the gateway does (canonical JSON)."""
    doc = json.load(sys.stdin)
    doc["inference"]["label"] = "1" if doc["inference"]["label"] == "0" else "0"
    print(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    {"newest": newest_report_id, "text": claim_text, "tamper": tamper}[sys.argv[1]]()
