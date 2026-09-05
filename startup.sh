#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ORGS=3
SAMPLES=50
MAX_ORGS=20
MAX_SAMPLES=100000
SKIP_CALIPER=false

usage() {
  cat <<'EOF'
startup.sh — blockchain layer only: Fabric network + all three gateways
(fabric_gateway sidecar, ipfs_gateway, blockchain_gateway), then a
validation load/benchmark against it.

For the application pipeline (Kafka, flagging-engine, submission-worker,
fact-checking-service, monitoring), run `docker compose up -d --build`
separately — see docker-compose.yml.

  --orgs N            founding org limit [default 3 & max 20]
  --test-samples N    validation requests to drive against gateway,
                      [default 50 & max 100000]
                      (Caliper Scaler: writes=N*10 & reads=N*20)
  --skip-caliper      skip Caliper benchmark, run only load-http.py
  --help              show this
EOF
  exit 0
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help) usage ;;
    --orgs) shift; ORGS="${1:-}" ;;
    --test-samples) shift; SAMPLES="${1:-}" ;;
    --skip-caliper) SKIP_CALIPER=true ;;
    *) echo "ERROR: unknown flag '$1' (try --help)" >&2; exit 1 ;;
  esac
  shift
done

if ! [[ "${ORGS}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --orgs must be an integer (got '${ORGS}')" >&2
  exit 1
fi
if ! [[ "${SAMPLES}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --test-samples must be an integer (got '${SAMPLES}')" >&2
  exit 1
fi
if [ "${ORGS}" -lt 1 ] || [ "${ORGS}" -gt "${MAX_ORGS}" ]; then
  echo "ERROR: --orgs must be 1..${MAX_ORGS}" >&2
  exit 1
fi
if [ "${SAMPLES}" -lt 1 ] || [ "${SAMPLES}" -gt "${MAX_SAMPLES}" ]; then
  echo "ERROR: --test-samples must be 1..${MAX_SAMPLES}" >&2
  exit 1
fi

mkdir -p results/local

echo ">> [1/7] Ensuring Docker networks exist..."
"${SCRIPT_DIR}/blockchain/scripts/ensure-networks.sh"

echo ">> [2/7] Provisioning ${ORGS}-org Fabric network..."
DEPLOY_ARGS=()
if [ "${ORGS}" -ne 3 ]; then
  DEPLOY_ARGS+=(--orgs "${ORGS}")
fi
"${SCRIPT_DIR}/blockchain/scripts/deploy.sh" "${DEPLOY_ARGS[@]:-up}"

echo ">> [3/7] Starting IPFS + IPFS Gateway..."
"${SCRIPT_DIR}/blockchain/scripts/start-ipfs.sh"
"${SCRIPT_DIR}/blockchain/scripts/start-ipfs-gateway.sh"

echo ">> [4/7] Bootstrapping API keys for org1..org${ORGS} (idempotent)..."
ORG_ARGS=()
for n in $(seq 1 "${ORGS}"); do ORG_ARGS+=("org${n}"); done
"${SCRIPT_DIR}/blockchain/scripts/bootstrap-keys.sh" "${ORG_ARGS[@]}"

echo ">> [5/7] Starting Fabric Gateway SDK sidecar + blockchain gateway..."
ORG_LIST="$(IFS=,; echo "${ORG_ARGS[*]}")"
export ORGS_LIST="${ORG_LIST}"
"${SCRIPT_DIR}/blockchain/scripts/start-gateway-service.sh" up
"${SCRIPT_DIR}/blockchain/scripts/start-blockchain-gateway.sh"

echo ">> Checking API gateway reachability..."
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
GW_STATUS="$(curl -s -m 5 -o /dev/null -w "%{http_code}" "${API_BASE_URL}/api/status" -H "X-API-Key: stress-key")"
if [ "${GW_STATUS}" != "200" ]; then
  echo "ERROR: API gateway unreachable (HTTP ${GW_STATUS})." >&2
  exit 1
fi

echo ">> [6/7] Quick validation load (orgs=${ORGS}, test-samples=${SAMPLES})..."
"${SCRIPT_DIR}/benchmarks/load-http.py" \
  --base "${API_BASE_URL}" \
  --samples "${SAMPLES}" \
  --output results/local/http.csv

if [ "${SKIP_CALIPER}" = false ]; then
  echo ">> [7/7] Caliper benchmark (writes=$((SAMPLES * 10)), reads=$((SAMPLES * 20)))..."
  "${SCRIPT_DIR}/benchmarks/caliper/gen-caliper-config.sh" --orgs "${ORGS}" --samples "${SAMPLES}"
  if ! "${SCRIPT_DIR}/benchmarks/caliper/run-caliper.sh"; then
    echo "ERROR: Caliper benchmark failed (exit code $?). Check logs above." >&2
    exit 1
  fi
else
  echo ">> [7/7] Skipping Caliper (--skip-caliper)."
fi

echo ">> Done. Teardown with:  ${SCRIPT_DIR}/blockchain/scripts/deploy.sh down"
