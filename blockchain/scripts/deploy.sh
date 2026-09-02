#!/usr/bin/env bash

set -euo pipefail
if ! command -v jq >/dev/null 2>&1 && [ -x "${HOME}/.local/bin/jq" ]; then

  export PATH="${HOME}/.local/bin:${PATH}"

fi

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: 'jq' not found. Install it" >&2
  exit 1

fi

if docker compose version >/dev/null 2>&1; then
  export CONTAINER_CLI_COMPOSE="docker compose"

fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHAINCODE_PATH="${PROJECT_ROOT}/chaincode/misinformation/go"
export FABRIC_SAMPLES="${FABRIC_SAMPLES:-${PROJECT_ROOT}/fabric-samples}"
TEST_NETWORK="${FABRIC_SAMPLES}/test-network"
CC_NAME="misinformation"
CC_VERSION="2.2"
CC_SEQUENCE="1"
CC_SRC_LANGUAGE="go"
CHANNEL_NAME="mychannel"
STATE_DB="${STATE_DB:--s couchdb}"
STATE_DB_ARGS=${STATE_DB}
CC_ENDORSEMENT_POLICY=""
THREE_ORG="1"
FOUNDING_LIMIT=3
cd "${TEST_NETWORK}"
RESET_NETWORK="1"

case "${1:-up}" in
  up|"")
    ;;
  down)
    echo ">> Tearing down the network..."
    ./network.sh down
    exit 0
    ;;
  -2)
    THREE_ORG=""
    ;;
  --orgs)

    if [ "$#" -lt 2 ]; then
      echo "ERROR: --orgs requires a value (e.g. --orgs 6)" >&2
      exit 1

    fi

    FOUNDING_LIMIT="${2}"
    if ! [[ "${FOUNDING_LIMIT}" =~ ^[0-9]+$ ]] || [ "${FOUNDING_LIMIT}" -lt 1 ]; then
      echo "ERROR: --orgs must be a positive integer, got '${FOUNDING_LIMIT}'" >&2
      exit 1
    fi
    ;;
  *)
    echo "ERROR: unknown argument '${1}' (expected up | down | -2 | --orgs N)" >&2
    exit 1
    ;;
esac

if [ -n "${RESET_NETWORK}" ]; then
  echo ">> Resetting any existing network (fresh ledger)..."
  ./network.sh down >/dev/null 2>&1 || true
  if docker network ls --format '{{.Name}}' | grep -qx 'fabric_test'; then
    echo ">> Removing stale fabric_test docker network (wrong compose labels)..."
    docker network rm fabric_test >/dev/null 2>&1 || true
  fi
fi

echo ">> Starting the test network..."
./network.sh up ${STATE_DB_ARGS}
echo ">> Creating channel ${CHANNEL_NAME}..."
./network.sh createChannel -c "${CHANNEL_NAME}"
echo ">> Deploying ${CC_NAME} v${CC_VERSION} (${CC_SRC_LANGUAGE})..."
./network.sh deployCC -c "${CHANNEL_NAME}" \
  -ccn "${CC_NAME}" \
  -ccv "${CC_VERSION}" \
  -ccs "${CC_SEQUENCE}" \
  -ccl "${CC_SRC_LANGUAGE}" \
  -ccp "${CHAINCODE_PATH}" \
  ${CC_ENDORSEMENT_POLICY}

if [ -n "${THREE_ORG}" ]; then
  echo ">> Bringing up org3 (addOrg3)..."
  (cd "${TEST_NETWORK}/addOrg3" && ./addOrg3.sh up)
  echo ">> Switching to a 2-of-3 endorsement policy..."
  "${SCRIPT_DIR}/onboard-org3.sh"
fi

if [ -n "${THREE_ORG}" ] && [ "${FOUNDING_LIMIT}" -gt 3 ]; then
  echo ">> Adding peer orgs to the channel..."
  "${SCRIPT_DIR}/add-orgs.sh" --orgs "${FOUNDING_LIMIT}"
fi

if [ "${FOUNDING_LIMIT}" -ne 3 ]; then
  echo ">> Setting founding org limit to ${FOUNDING_LIMIT} (stress-test mode)..."
  export FABRIC_CFG_PATH="${TEST_NETWORK}/../config"
  export CORE_PEER_TLS_ENABLED=true
  export CORE_PEER_LOCALMSPID="Org1MSP"
  export CORE_PEER_TLS_ROOTCERT_FILE="${TEST_NETWORK}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"
  export CORE_PEER_MSPCONFIGPATH="${TEST_NETWORK}/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp"
  export CORE_PEER_ADDRESS="localhost:7051"
  payload=$(python3 -c "import json,sys; print(json.dumps({'function':'SetFoundingOrgLimit','Args':['${FOUNDING_LIMIT}']}))")
  peer chaincode invoke -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
    --tls --cafile "${TEST_NETWORK}/organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem" \
    -C "${CHANNEL_NAME}" -n "${CC_NAME}" \
    --peerAddresses localhost:7051 --tlsRootCertFiles "${CORE_PEER_TLS_ROOTCERT_FILE}" \
    --peerAddresses localhost:9051 --tlsRootCertFiles "${TEST_NETWORK}/organizations/peerOrganizations/org2.example.com/peers/peer0.org2.example.com/tls/ca.crt" \
    --waitForEvent -c "${payload}" >/dev/null

fi

"${SCRIPT_DIR}/register-orgs.sh" --limit "${FOUNDING_LIMIT}"
"${SCRIPT_DIR}/gen-explorer-config.sh" --orgs "${FOUNDING_LIMIT}"
echo

if [ -n "${THREE_ORG}" ]; then
  echo ">> Network up and chaincode ${CC_NAME} committed (${FOUNDING_LIMIT}-org, OutOf(2,...) policy)."
  if [ "${FOUNDING_LIMIT}" -ne 3 ]; then
    echo ">> Founding org limit set to ${FOUNDING_LIMIT}."

  fi

else
  echo ">> Network up and chaincode ${CC_NAME} committed (2-org, AND policy)."

fi

echo
