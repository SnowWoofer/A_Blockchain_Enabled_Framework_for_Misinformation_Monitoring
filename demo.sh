#!/usr/bin/env bash

# demo.sh — the web client without Hyperledger Fabric, for laptop demos and UI work: blockchain gateway on its in-memory mock ledger (FABRIC_BACKEND=mock), fact-checking service and the TLS web edge, seeded with sample claims.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="docker compose"
ORGS=3
CLAIMS=12
CMD="${1:-up}"
shift || true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --orgs) shift; ORGS="${1:-}" ;;
    --claims) shift; CLAIMS="${1:-}" ;;
    *) echo "ERROR: unknown flag '$1'" >&2; exit 1 ;;
  esac
  shift
done

API_DIR="${SCRIPT_DIR}/apps/blockchain_gateway/app/v1-0-0/api"
FILES=(-f apps/web-client/docker-compose.yaml -f apps/fact-checking-service/docker-compose.yaml)

down() {
  (cd "${SCRIPT_DIR}" && ${COMPOSE} -f apps/web-client/docker-compose.yaml rm -sf web >/dev/null 2>&1 || true)
  (cd "${SCRIPT_DIR}" && ${COMPOSE} -f apps/fact-checking-service/docker-compose.yaml rm -sf fact-checking-service >/dev/null 2>&1 || true)
  "${SCRIPT_DIR}/blockchain/scripts/start-blockchain-gateway.sh" down
}

case "${CMD}" in
  down) down; exit 0 ;;
  reset)
    down
    rm -f "${API_DIR}/mock-ledger.json" "${API_DIR}/offchain.db" "${API_DIR}/users.db" "${API_DIR}/audit.db"
    echo ">> Demo ledger, documents, accounts and audit log removed."
    exit 0 ;;
  up) ;;
  *) echo "usage: $0 up|down|reset [--orgs N] [--claims N]" >&2; exit 1 ;;
esac

[[ "${ORGS}" =~ ^[0-9]+$ ]] && [ "${ORGS}" -ge 3 ] || { echo "ERROR: --orgs must be >= 3" >&2; exit 1; }

echo ">> Networks + secrets..."
"${SCRIPT_DIR}/blockchain/scripts/ensure-networks.sh"
"${SCRIPT_DIR}/blockchain/scripts/gen-secrets.sh"
. "${SCRIPT_DIR}/blockchain/scripts/lib/env.sh"

ORG_ARGS=()
for n in $(seq 1 "${ORGS}"); do ORG_ARGS+=("org${n}"); done
export ORGS_LIST="$(IFS=,; echo "${ORG_ARGS[*]}")"
export FABRIC_BACKEND=mock
FRESH_LEDGER=""
[ -f "${API_DIR}/mock-ledger.json" ] || FRESH_LEDGER=1

echo ">> API keys (no well-known benchmark keys in the demo)..."
"${SCRIPT_DIR}/blockchain/scripts/bootstrap-keys.sh" --no-dev-keys "${ORG_ARGS[@]}" >/dev/null

echo ">> Blockchain gateway (mock ledger)..."
"${SCRIPT_DIR}/blockchain/scripts/start-blockchain-gateway.sh"

echo ">> Fact-checking service + web client..."
(cd "${SCRIPT_DIR}" && ${COMPOSE} "${FILES[@]}" up -d --build) | tail -3

echo ">> Accounts..."
CREDS="${SCRIPT_DIR}/secrets/initial-credentials.txt"
(umask 077; docker exec blockchain-gateway python api/manage.py seed --orgs "${ORGS_LIST}" | tee -a "${CREDS}")

if [ -n "${FRESH_LEDGER}" ]; then
  echo ">> Sample claims..."
  python3 "${SCRIPT_DIR}/blockchain/scripts/demo-seed.py" --gateway http://127.0.0.1:8000 \
    --db "${API_DIR}/offchain.db" --orgs "${ORGS}" --claims "${CLAIMS}"
fi

echo
echo ">> Demo ready: https://localhost:${WEB_HTTPS_PORT:-8443}  (self-signed certificate: accept the browser warning)"
echo "   Sign in with an account from ${CREDS}"
echo "   e.g. checker.org2 to review claims, official.org2 for the officials' console."
