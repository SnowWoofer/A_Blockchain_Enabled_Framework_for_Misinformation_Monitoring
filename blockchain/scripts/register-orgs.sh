#!/usr/bin/env bash
# register-orgs.sh — register stakeholder orgs 1..N on-chain via the
# "misinformation" chaincode. The chaincode's SubmitReport rejects work from any
# caller whose MSP is not a registered org, so this must run after the network
# is up (this is wired into deploy.sh).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEST_NETWORK="${FABRIC_SAMPLES:-${PROJECT_ROOT}/fabric-samples}/test-network"

CC_NAME="misinformation"
CHANNEL_NAME="mychannel"
LIMIT=3

while [ "$#" -gt 0 ]; do
  case "$1" in
    --limit) shift; LIMIT="${1:-}" ;;
    --channel) shift; CHANNEL_NAME="${1:-}" ;;
    --cc-name) shift; CC_NAME="${1:-}" ;;
    *) echo "ERROR: unknown arg '$1'" >&2; exit 1 ;;
  esac
  shift
done

if ! [[ "${LIMIT}" =~ ^[0-9]+$ ]] || [ "${LIMIT}" -lt 1 ]; then
  echo "ERROR: --limit must be a positive integer (got '${LIMIT}')" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1 && [ -x "${HOME}/.local/bin/jq" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi
export PATH="${TEST_NETWORK}/../bin:${PATH}"
export FABRIC_CFG_PATH="${TEST_NETWORK}/../config"
export CORE_PEER_TLS_ENABLED=true
ORDERER_CA="${TEST_NETWORK}/organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem"
ORG1_TLS="${TEST_NETWORK}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"
ORG2_TLS="${TEST_NETWORK}/organizations/peerOrganizations/org2.example.com/peers/peer0.org2.example.com/tls/ca.crt"

org_peer_port() { echo $((7051 + 2000 * ($1 - 1))); }

use_org() {
  local n="$1"
  local port
  port="$(org_peer_port "${n}")"
  export CORE_PEER_LOCALMSPID="Org${n}MSP"
  export CORE_PEER_TLS_ROOTCERT_FILE="${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/peers/peer0.org${n}.example.com/tls/ca.crt"
  export CORE_PEER_MSPCONFIGPATH="${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/users/Admin@org${n}.example.com/msp"
  export CORE_PEER_ADDRESS="localhost:${port}"
}

payload=$(python3 -c "import json,sys; print(json.dumps({'function':'RegisterOrg','Args':[]}))")

echo ">> Registering stakeholder orgs (1..${LIMIT})..."
for n in $(seq 1 "${LIMIT}"); do
  use_org "${n}"
  echo -n "  org${n}: "
  peer chaincode invoke -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
    --tls --cafile "${ORDERER_CA}" \
    -C "${CHANNEL_NAME}" -n "${CC_NAME}" \
    --peerAddresses localhost:7051 --tlsRootCertFiles "${ORG1_TLS}" \
    --peerAddresses localhost:9051 --tlsRootCertFiles "${ORG2_TLS}" \
    --waitForEvent -c "${payload}" 2>&1 | tail -1
done

echo ">> Register-org done (orgs 1..${LIMIT})."