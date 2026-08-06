#!/usr/bin/env bash
# Deploy the misinformation chaincode to the Hyperledger Fabric test-network.
#
# Track B, milestone B4-B6: reuses the canonical fabric-samples test-network
# (Raft consensus, LevelDB) as the "deliberately simple, platform-agnostic" PoC
# topology. CLI-only — the REST/api layer is deliberately deferred (B5).
#
# Lifecycle: package -> install (all orgs) -> approve (all orgs) -> commit.
#
# Usage:
#   ./scripts/deploy.sh            # 2-org network + deploy chaincode (v1.1)
#   ./scripts/deploy.sh down       # tear down the network (containers + artifacts)
#   ./scripts/onboard-org3.sh      # add a 3rd org + switch to a 2/3 endorsement policy

set -euo pipefail

# jq is required by the fabric lifecycle scripts; add a user-local install if present.
if ! command -v jq >/dev/null 2>&1 && [ -x "${HOME}/.local/bin/jq" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: 'jq' not found. Install it (e.g. jq-linux-amd64 -> ~/.local/bin/jq)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHAINCODE_PATH="${PROJECT_ROOT}/chaincode/misinformation/go"
FABRIC_SAMPLES="${FABRIC_SAMPLES:-${HOME}/fabric-samples}"
TEST_NETWORK="${FABRIC_SAMPLES}/test-network"

CC_NAME="misinformation"
CC_VERSION="2.1"
CC_SEQUENCE="1"
CC_SRC_LANGUAGE="go"
CHANNEL_NAME="mychannel"

# State database: CouchDB (rich JSON/Mango queries for the API + public
# dashboard, v2 §6). Set STATE_DB="" for LevelDB.
STATE_DB="${STATE_DB:--s couchdb}"
STATE_DB_ARGS=${STATE_DB}

# Endorsement policy for the 2-org deployment. Default (empty) = AND(Org1, Org2) —
# both orgs must endorse every transaction. After running onboard-org3.sh the
# policy becomes OutOf(2, Org1MSP, Org2MSP, Org3MSP) — a 2-of-3 quorum.
CC_ENDORSEMENT_POLICY=""

cd "${TEST_NETWORK}"

if [[ "${1:-up}" == "down" ]]; then
  echo ">> Tearing down the network..."
  ./network.sh down
  exit 0
fi

echo ">> Starting the 2-org test network (Raft consensus)..."
# shellcheck disable=SC2086
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

echo
echo ">> Network up and chaincode ${CC_NAME} committed (2-org, AND policy)."
echo "   Next: ./scripts/onboard-org3.sh   to add a 3rd org + 2/3 endorsement."
echo
