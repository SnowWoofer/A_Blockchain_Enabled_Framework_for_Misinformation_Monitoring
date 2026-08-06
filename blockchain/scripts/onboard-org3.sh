#!/usr/bin/env bash
# Onboard Org3: install + approve the misinformation chaincode on all THREE
# orgs and re-commit with a 2-of-3 endorsement policy.
#
# Prereqs (in order):
#   1. ./scripts/deploy.sh              # 2-org network + chaincode committed
#   2. test-network/addOrg3/addOrg3.sh up   # org3 peer running + on channel
# Then: ./scripts/onboard-org3.sh
#
# Outcome: endorsement policy becomes OutOf(2, Org1MSP, Org2MSP, Org3MSP) —
# a quorum of 2 of the 3 stakeholder orgs must endorse every transaction.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHAINCODE_PATH="${PROJECT_ROOT}/chaincode/misinformation/go"
TEST_NETWORK="${FABRIC_SAMPLES:-${HOME}/fabric-samples}/test-network"
ADD_ORG3="${TEST_NETWORK}/addOrg3"

CC_NAME="misinformation"
CC_VERSION="2.1"
CC_SEQUENCE="2"
CHANNEL_NAME="mychannel"
POLICY="OutOf(2, 'Org1MSP.member','Org2MSP.member','Org3MSP.member')"

if ! command -v jq >/dev/null 2>&1 && [ -x "${HOME}/.local/bin/jq" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi
export PATH="${TEST_NETWORK}/../bin:${PATH}"
export FABRIC_CFG_PATH="${TEST_NETWORK}/../config"
export CORE_PEER_TLS_ENABLED=true

ORDERER_TLS="${TEST_NETWORK}/organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem"
ORG1_TLS="${TEST_NETWORK}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"
ORG2_TLS="${TEST_NETWORK}/organizations/peerOrganizations/org2.example.com/peers/peer0.org2.example.com/tls/ca.crt"
ORG3_TLS="${TEST_NETWORK}/organizations/peerOrganizations/org3.example.com/peers/peer0.org3.example.com/tls/ca.crt"

use_org() {
  local org="$1"
  case "${org}" in
    org1)
      export CORE_PEER_LOCALMSPID="Org1MSP"
      export CORE_PEER_TLS_ROOTCERT_FILE="${ORG1_TLS}"
      export CORE_PEER_MSPCONFIGPATH="${TEST_NETWORK}/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp"
      export CORE_PEER_ADDRESS="localhost:7051"
      ;;
    org2)
      export CORE_PEER_LOCALMSPID="Org2MSP"
      export CORE_PEER_TLS_ROOTCERT_FILE="${ORG2_TLS}"
      export CORE_PEER_MSPCONFIGPATH="${TEST_NETWORK}/organizations/peerOrganizations/org2.example.com/users/Admin@org2.example.com/msp"
      export CORE_PEER_ADDRESS="localhost:9051"
      ;;
    org3)
      export CORE_PEER_LOCALMSPID="Org3MSP"
      export CORE_PEER_TLS_ROOTCERT_FILE="${ORG3_TLS}"
      export CORE_PEER_MSPCONFIGPATH="${TEST_NETWORK}/organizations/peerOrganizations/org3.example.com/users/Admin@org3.example.com/msp"
      export CORE_PEER_ADDRESS="localhost:11051"
      ;;
  esac
}

echo ">> Packaging chaincode ${CC_NAME} v${CC_VERSION}..."
cd "${CHAINCODE_PATH}"
go mod vendor
tar czf "${TEST_NETWORK}/misinformation-org3.tar.gz" --exclude=vendor --exclude='*.tar.gz' ../.. 2>/dev/null || true
cd "${TEST_NETWORK}"
peer lifecycle chaincode package misinformation-org3.tar.gz \
  --path "${CHAINCODE_PATH}" --lang golang --label "${CC_NAME}_${CC_VERSION}" 2>&1 | tail -1

PACKAGE_ID=$(peer lifecycle chaincode calculatepackageid misinformation-org3.tar.gz)
echo ">> Package ID: ${PACKAGE_ID}"

for org in org1 org2 org3; do
  echo ">> Install on ${org}..."
  use_org "${org}"
  # "already installed" is fine (idempotent); only a genuine failure aborts.
  if ! out=$(peer lifecycle chaincode install misinformation-org3.tar.gz 2>&1); then
    if ! echo "${out}" | grep -q "already successfully installed"; then
      echo "${out}" >&2
      exit 1
    fi
  fi
done

for org in org1 org2 org3; do
  echo ">> Approve on ${org}..."
  use_org "${org}"
  peer lifecycle chaincode approveformyorg -o localhost:7050 \
    --ordererTLSHostnameOverride orderer.example.com \
    --channelID "${CHANNEL_NAME}" --name "${CC_NAME}" \
    --version "${CC_VERSION}" --package-id "${PACKAGE_ID}" \
    --sequence "${CC_SEQUENCE}" \
    --signature-policy "${POLICY}" \
    --tls --cafile "${ORDERER_TLS}" \
    --waitForEvent 2>&1 | tail -1
done

echo ">> Checking commit readiness..."
use_org org1
peer lifecycle chaincode checkcommitreadiness -o localhost:7050 \
  --ordererTLSHostnameOverride orderer.example.com \
  --channelID "${CHANNEL_NAME}" --name "${CC_NAME}" \
  --version "${CC_VERSION}" --sequence "${CC_SEQUENCE}" \
  --signature-policy "${POLICY}" \
  --tls --cafile "${ORDERER_TLS}" --output json 2>&1 | tail -1

echo ">> Committing with 2/3 endorsement policy..."
peer lifecycle chaincode commit -o localhost:7050 \
  --ordererTLSHostnameOverride orderer.example.com \
  --channelID "${CHANNEL_NAME}" --name "${CC_NAME}" \
  --version "${CC_VERSION}" --sequence "${CC_SEQUENCE}" \
  --signature-policy "${POLICY}" \
  --peerAddresses localhost:7051 --tlsRootCertFiles "${ORG1_TLS}" \
  --peerAddresses localhost:9051 --tlsRootCertFiles "${ORG2_TLS}" \
  --peerAddresses localhost:11051 --tlsRootCertFiles "${ORG3_TLS}" \
  --tls --cafile "${ORDERER_TLS}" \
  --waitForEvent 2>&1 | tail -2

echo ">> Query committed definition on each org..."
for org in org1 org2 org3; do
  use_org "${org}"
  echo -n "  ${org}: "
  peer lifecycle chaincode querycommitted --channelID "${CHANNEL_NAME}" --name "${CC_NAME}" \
    2>&1 | grep -o "Endorsement Plugin.*" | head -1
done

echo
echo "Org3 onboarded. Endorsement policy is now: ${POLICY}"
