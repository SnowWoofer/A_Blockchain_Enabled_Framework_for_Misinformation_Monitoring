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
startup.sh — misinformation-monitoring pipeline.

  --orgs N          founding org limit [default 3 & max 20]
  --samples N       load samples/requests to drive, [default 50 & max 100000]
                    (Caliper Scaler: writes=N*10 & reads=N*20)
  --skip-caliper    skip Caliper benchmark, run only load-http.py
  --help            show this
EOF
  exit 0
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help) usage ;;
    --orgs) shift; ORGS="${1:-}" ;;
    --samples) shift; SAMPLES="${1:-}" ;;
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
  echo "ERROR: --samples must be an integer (got '${SAMPLES}')" >&2
  exit 1
fi
if [ "${ORGS}" -lt 1 ] || [ "${ORGS}" -gt "${MAX_ORGS}" ]; then
  echo "ERROR: --orgs must be 1..${MAX_ORGS}" >&2
  exit 1
fi
if [ "${SAMPLES}" -lt 1 ] || [ "${SAMPLES}" -gt "${MAX_SAMPLES}" ]; then
  echo "ERROR: --samples must be 1..${MAX_SAMPLES}" >&2
  exit 1
fi

echo ">> [1/5] Provisioning ${ORGS}-org network..."
DEPLOY_ARGS=()
if [ "${ORGS}" -ne 3 ]; then
  DEPLOY_ARGS+=(--orgs "${ORGS}")
fi
"${SCRIPT_DIR}/blockchain/scripts/deploy.sh" "${DEPLOY_ARGS[@]:-up}"

echo ">> [2/5] Checking API gateway reachability..."
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
GW_STATUS="$(curl -s -m 5 -o /dev/null -w "%{http_code}" "${API_BASE_URL}/api/status" -H "X-API-Key: stress-key")"
if [ "${GW_STATUS}" != "200" ]; then
  echo "ERROR: API gateway unreachable (HTTP ${GW_STATUS}). Start it first:" >&2
  echo "  cd apps/ai_service/app/v1-0-0/api" >&2
  echo "  ../../../../../venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring/bin/uvicorn server:app --host 0.0.0.0 --port 8000" >&2
  echo "  (HTTP 401 = API keys not bootstrapped; run: blockchain/scripts/bootstrap-keys.sh org1 org2 org3)" >&2
  exit 1
fi

echo ">> [3/5] Quick validation load (orgs=${ORGS}, samples=${SAMPLES})..."
"${SCRIPT_DIR}/benchmarks/load-http.py" \
  --base "${API_BASE_URL}" \
  --samples "${SAMPLES}"

if [ "${SKIP_CALIPER}" = false ]; then
  echo ">> [4/5] Caliper benchmark (writes=$((SAMPLES * 10)), reads=$((SAMPLES * 20)))..."
  "${SCRIPT_DIR}/benchmarks/caliper/gen-caliper-config.sh" --orgs "${ORGS}" --samples "${SAMPLES}"
  if ! "${SCRIPT_DIR}/benchmarks/caliper/run-caliper.sh"; then
    echo "ERROR: Caliper benchmark failed (exit code $?). Check logs above." >&2
    exit 1
  fi
  echo ">> [5/5] Done. Teardown with:  ${SCRIPT_DIR}/blockchain/scripts/deploy.sh down"
else
  echo ">> [4/5] Skipping Caliper (--skip-caliper)."
  echo ">> [5/5] Done. Teardown with:  ${SCRIPT_DIR}/blockchain/scripts/deploy.sh down"
fi