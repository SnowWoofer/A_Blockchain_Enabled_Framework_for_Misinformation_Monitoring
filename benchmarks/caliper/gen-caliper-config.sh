#!/usr/bin/env bash
# gen-caliper-config.sh — regenerate the Caliper Fabric connection profile
# for an N-organization consortium, and optionally scale benchmark rounds.
#
#   ./gen-caliper-config.sh                    # default: org1..org3, samples=50
#   ./gen-caliper-config.sh --orgs 10          # 10 orgs, default samples
#   ./gen-caliper-config.sh --samples 100      # 3 orgs, scaled samples
#   ./gen-caliper-config.sh --orgs 6 --samples 75
#
# Writes:
#   - networks/fabric/ccp.json (connection profile)
#   - benchmarks/misinformation-benchmark.yaml (scaled benchmark config)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OUT_CCP="${SCRIPT_DIR}/networks/fabric/ccp.json"
OUT_BENCH="${SCRIPT_DIR}/benchmarks/misinformation-benchmark.yaml"

N=3
SAMPLES=50

while [ "$#" -gt 0 ]; do
  case "$1" in
    --orgs) shift; N="${1:-}" ;;
    --samples) shift; SAMPLES="${1:-}" ;;
    *) echo "ERROR: unknown arg '$1'" >&2; exit 1 ;;
  esac
  shift
done

if ! [[ "${N}" =~ ^[0-9]+$ ]] || [ "${N}" -lt 1 ]; then
  echo "ERROR: --orgs must be a positive integer (got '${N}')" >&2
  exit 1
fi
if ! [[ "${SAMPLES}" =~ ^[0-9]+$ ]] || [ "${SAMPLES}" -lt 1 ]; then
  echo "ERROR: --samples must be a positive integer (got '${SAMPLES}')" >&2
  exit 1
fi

mkdir -p "$(dirname "${OUT_CCP}")"
mkdir -p "$(dirname "${OUT_BENCH}")"

# Scale factor: base samples=50 gives 500 writes / 1000 reads
WRITES=$((SAMPLES * 10))
READS=$((SAMPLES * 20))

python3 - "${N}" > "${OUT_CCP}" <<'PY'
import json, sys
n = int(sys.argv[1])
base = "/crypto/peerOrganizations"
organizations = {}
peer_entries = {}
for i in range(1, n + 1):
    org = f"org{i}"
    msp = f"Org{i}MSP"
    port = 7051 + 2000 * (i - 1)
    peer = f"peer0.{org}.example.com"
    admin_msp = f"{base}/{org}.example.com/users/Admin@{org}.example.com/msp"
    organizations[msp] = {
        "mspid": msp,
        "peers": [peer],
        "identities": {
            "certificates": [
                {
                    "name": "Admin",
                    "admin": True,
                    "clientPrivateKey": {"path": f"{admin_msp}/keystore/priv_sk"},
                    "clientSignedCert": {"path": f"{admin_msp}/signcerts/Admin@{org}.example.com-cert.pem"},
                }
            ]
        },
    }
    peer_entries[peer] = {
        "url": f"grpcs://{peer}:{port}",
        "tlsCACerts": {"path": f"{base}/{org}.example.com/peers/{peer}/tls/ca.crt"},
        "grpcOptions": {"hostnameOverride": peer},
    }

profile = {
    "name": "test-network",
    "version": "1.0.0",
    "client": {
        "organization": "Org1MSP",
        "connection": {"timeout": {"peer": {"endorser": "300"}, "orderer": "300"}},
    },
    "organizations": organizations,
    "peers": peer_entries,
}
json.dump(profile, sys.stdout, indent=2)
print()
PY

python3 - "${WRITES}" "${READS}" > "${OUT_BENCH}" <<'PY'
import json, sys
writes = int(sys.argv[1])
reads = int(sys.argv[2])
config = {
    "test": {
        "name": "misinformation-contract-benchmark",
        "description": f"Write + read benchmark for the misinformation chaincode on mychannel.\n    Round 1 anchors synthetic reports on-chain (SubmitReport, endorsed 2-of-3).\n    Round 2 hammers the read path (QueryAllReports via CouchDB world state).\n    Configured: {writes} writes @ 25 TPS, {reads} reads @ 50 TPS.",
        "workers": {"number": 2},
        "rounds": [
            {
                "label": "submit-report-write",
                "description": "Anchor synthetic misinformation reports on-chain",
                "txNumber": writes,
                "rateControl": {"type": "fixed-rate", "opts": {"tps": 25}},
                "workload": {"module": "workload/submitReport.js", "arguments": {"contractId": "misinformation"}},
            },
            {
                "label": "query-all-reports-read",
                "description": "Range-scan the ledger through the gateway peer",
                "txNumber": reads,
                "rateControl": {"type": "fixed-rate", "opts": {"tps": 50}},
                "workload": {"module": "workload/queryAllReports.js", "arguments": {"contractId": "misinformation"}},
            },
        ],
    }
}
json.dump(config, sys.stdout, indent=2)
print()
PY

echo ">> Caliper connection profile written for org1..${N}: ${OUT_CCP}"
echo ">> Caliper benchmark config written (writes=${WRITES}, reads=${READS}): ${OUT_BENCH}"
echo ">> NOTE: if N > 3, also extend networks/fabric/test-network.yaml with the extra organizations."
