#!/usr/bin/env bash
# gen-caliper-config.sh, regenerate the Caliper Fabric connection profile

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OUT_CCP="${SCRIPT_DIR}/networks/fabric/ccp.json"
OUT_BENCH="${SCRIPT_DIR}/benchmarks/misinformation-benchmark.yaml"
OUT_NET="${SCRIPT_DIR}/networks/fabric/test-network.yaml"
# Host path of the test-network crypto that docker-compose mounts at /crypto.
CRYPTO_HOST="${SCRIPT_DIR}/../../blockchain/fabric-samples/test-network/organizations"
# Caliper signs as this role's identity (register-roles.sh): the chaincode only
# lets official / fact_checker submit, and the CA-mode Admin has no role attribute.
CALIPER_ROLE="${CALIPER_ROLE:-fact_checker}"

N=3
SAMPLES=50
BENCHMARK_ONLY=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --orgs) shift; N="${1:-}" ;;
    --samples) shift; SAMPLES="${1:-}" ;;
    --benchmark-only) BENCHMARK_ONLY=1 ;;
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

# Scale factor: base samples=50 gives 500 writes / 500 reads
WRITES=$((SAMPLES * 10))
READS=$((SAMPLES * 10))

if [ "${BENCHMARK_ONLY}" -eq 0 ]; then
  python3 - "${N}" "${CRYPTO_HOST}" "${CALIPER_ROLE}" "${OUT_NET}" > "${OUT_CCP}" <<'PY'
import json, os, sys
n = int(sys.argv[1])
crypto_host, role, out_net = sys.argv[2], sys.argv[3], sys.argv[4]
base = "/crypto/peerOrganizations"


def identity(i):
    # Fabric CA (network.sh up -ca) names the cert cert.pem and the key <hash>_sk.
    org = f"org{i}"
    user = f"{role}{i}@{org}.example.com"
    rel = f"{org}.example.com/users/{user}/msp"
    keystore = os.path.join(crypto_host, "peerOrganizations", rel, "keystore")
    keys = sorted(os.listdir(keystore)) if os.path.isdir(keystore) else []
    key = "priv_sk" if "priv_sk" in keys else next((k for k in keys if k.endswith("_sk")), None)
    if key is None:
        sys.exit(f"ERROR: no private key for {user} in {keystore}; deploy the network first (startup.sh runs register-roles.sh)")
    return {
        "name": f"{role}{i}",
        "clientPrivateKey": {"path": f"{base}/{rel}/keystore/{key}"},
        "clientSignedCert": {"path": f"{base}/{rel}/signcerts/cert.pem"},
    }


organizations = {}
peer_entries = {}
for i in range(1, n + 1):
    org = f"org{i}"
    msp = f"Org{i}MSP"
    port = 7051 + 2000 * (i - 1)
    peer = f"peer0.{org}.example.com"
    organizations[msp] = {
        "mspid": msp,
        "peers": [peer],
        "identities": {"certificates": [identity(i)]},
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

# Caliper network config: Org1 is the client organisation.
client = identity(1)
with open(out_net, "w") as f:
    f.write(f"""name: Caliper Benchmarks
version: "2.0.0"

caliper:
  blockchain: fabric

info:
  Framework: Misinformation Monitoring Consortium
  Channel: mychannel
  Chaincode: misinformation
  Organizations: {n}
  Identity: {client['name']}@org1 ({role})

channels:
  - channelName: mychannel
    contracts:
      - id: misinformation

organizations:
  - mspid: Org1MSP
    identities:
      certificates:
        - name: '{client['name']}'
          clientPrivateKey:
            path: '{client['clientPrivateKey']['path']}'
          clientSignedCert:
            path: '{client['clientSignedCert']['path']}'
    connectionProfile:
      path: 'networks/fabric/ccp.json'
      discover: true
""")
PY
  echo ">> Caliper connection profile written for org1..${N}: ${OUT_CCP}"
  echo ">> Caliper network config written (identity ${CALIPER_ROLE}1@org1): ${OUT_NET}"
fi

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

echo ">> Caliper benchmark config written (writes=${WRITES}, reads=${READS}): ${OUT_BENCH}"
