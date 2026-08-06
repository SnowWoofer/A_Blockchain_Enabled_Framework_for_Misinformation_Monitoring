#!/usr/bin/env bash
# CLI smoke test for the misinformation chaincode (B5: no REST layer).
#
# Exercises the consortium review workflow end-to-end:
#   RegisterOrg (org1, org2) -> SubmitReport (org1) -> CastVote (org1, org2)
#   -> FinalizeReport (org2) -> queries (report, votes, all, count, history).
#
# Assumes the chaincode is deployed (see deploy.sh).
# Usage: ./scripts/smoke-test.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEST_NETWORK="${FABRIC_SAMPLES:-${HOME}/fabric-samples}/test-network"

CHANNEL="mychannel"
CC="misinformation"

# Sample values (a stable sha256 of "misinformation audit test row")
HASH="4f4d1a9c9c7c2f5e8f5b6c3d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f"
REPORT_ID="test-report-$(date +%s)"
LANGUAGE="nso"
LABEL="1"
CONFIDENCE="0.97"
MODEL="afroxlmr-large-nso-v1.0"
TS="2025-01-01T00:00:00Z"
OFF_URI="http://localhost:8000/api/reports/${REPORT_ID}"
VERDICT="accept"

export PATH="${TEST_NETWORK}/../bin:${PATH}"
export FABRIC_CFG_PATH="${TEST_NETWORK}/../config"
export CORE_PEER_TLS_ENABLED=true

ORDERER_TLS="${TEST_NETWORK}/organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem"
ORG1_TLS="${TEST_NETWORK}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"
ORG2_TLS="${TEST_NETWORK}/organizations/peerOrganizations/org2.example.com/peers/peer0.org2.example.com/tls/ca.crt"

# use_org <org1|org2|org3> switches the signing identity + peer address.
use_org() {
  local org="$1"
  case "${org}" in
    org2)
      export CORE_PEER_LOCALMSPID="Org2MSP"
      export CORE_PEER_TLS_ROOTCERT_FILE="${ORG2_TLS}"
      export CORE_PEER_MSPCONFIGPATH="${TEST_NETWORK}/organizations/peerOrganizations/org2.example.com/users/Admin@org2.example.com/msp"
      export CORE_PEER_ADDRESS="localhost:9051"
      ;;
    org3)
      export CORE_PEER_LOCALMSPID="Org3MSP"
      export CORE_PEER_TLS_ROOTCERT_FILE="${TEST_NETWORK}/organizations/peerOrganizations/org3.example.com/peers/peer0.org3.example.com/tls/ca.crt"
      export CORE_PEER_MSPCONFIGPATH="${TEST_NETWORK}/organizations/peerOrganizations/org3.example.com/users/Admin@org3.example.com/msp"
      export CORE_PEER_ADDRESS="localhost:11051"
      ;;
    *)
      export CORE_PEER_LOCALMSPID="Org1MSP"
      export CORE_PEER_TLS_ROOTCERT_FILE="${ORG1_TLS}"
      export CORE_PEER_MSPCONFIGPATH="${TEST_NETWORK}/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp"
      export CORE_PEER_ADDRESS="localhost:7051"
      ;;
  esac
}

ORG3_TLS="${TEST_NETWORK}/organizations/peerOrganizations/org3.example.com/peers/peer0.org3.example.com/tls/ca.crt"

# invoke <function> <args...> — endorsed by org1+org2 (2 of the 2/3 quorum),
# signed by the current org. After onboard-org3.sh the policy is OutOf(2,...)
# so org1+org2 satisfies it; pass --all for org1+org2+org3.
invoke() {
  local fn="$1"
  shift
  local payload
  payload=$(python3 -c "import json,sys; print(json.dumps({'function':sys.argv[1],'Args':sys.argv[2:]}))" "$fn" "$@")
  peer chaincode invoke -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
    --tls --cafile "${ORDERER_TLS}" \
    -C "${CHANNEL}" -n "${CC}" \
    --peerAddresses localhost:7051 --tlsRootCertFiles "${ORG1_TLS}" \
    --peerAddresses localhost:9051 --tlsRootCertFiles "${ORG2_TLS}" \
    ${ALL_PEERS:-} \
    --waitForEvent -c "${payload}"
}

# query <function> <args...>
query() {
  local fn="$1"
  shift
  local payload
  payload=$(python3 -c "import json,sys; print(json.dumps({'function':sys.argv[1],'Args':sys.argv[2:]}))" "$fn" "$@")
  peer chaincode query -C "${CHANNEL}" -n "${CC}" -c "${payload}"
}

echo "==> RegisterOrg (org1)"
use_org org1
invoke RegisterOrg

echo "==> RegisterOrg (org2)"
use_org org2
invoke RegisterOrg

echo "==> ListRegisteredOrgs"
query ListRegisteredOrgs

echo "==> SubmitReport (org1 signs)"
use_org org1
invoke SubmitReport "${REPORT_ID}" "${LANGUAGE}" "${HASH}" "${LABEL}" "${CONFIDENCE}" "${MODEL}" "${TS}" "${OFF_URI}"

echo "==> CastVote accept (org1)"
invoke CastVote "${REPORT_ID}" "${VERDICT}"

echo "==> CastVote accept (org2)"
use_org org2
invoke CastVote "${REPORT_ID}" "${VERDICT}"

echo "==> FinalizeReport (org2) — 2/2 registered orgs have voted -> FINAL"
invoke FinalizeReport "${REPORT_ID}"

echo "==> Query Report"
query QueryReport "${REPORT_ID}"

echo "==> Query Votes"
query QueryVotes "${REPORT_ID}"

echo "==> Query All Reports"
query QueryAllReports

echo "==> Report Count"
query GetReportCount

echo "==> Query Report History"
query QueryReportHistory "${REPORT_ID}"

echo
echo "Smoke test complete."
