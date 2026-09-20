#!/usr/bin/env bash
# run-caliper-matrix.sh == script that run Caliper benchmarks across multiple sample sizes
# against a single provisioned network.
#
# Usage:
#   ./run-caliper-matrix.sh --orgs 20 --samples "100 200 400 800 1000"
#
# Prerequisites:
#   - Network already running (startup.sh --orgs N --skip-caliper)
#   - docker network fabric_test exists

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
GEN_CONFIG="${SCRIPT_DIR}/gen-caliper-config.sh"
RUN_CALIPER="${SCRIPT_DIR}/run-caliper.sh"
COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

N=""
SAMPLES=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --orgs) shift; N="${1:-}" ;;
    --samples) shift; SAMPLES="${1:-}" ;;
    *) echo "ERROR: unknown arg '$1'" >&2; exit 1 ;;
  esac
  shift
done

if [ -z "${N}" ]; then
  echo "ERROR: --orgs N is required" >&2
  exit 1
fi
if [ -z "${SAMPLES}" ]; then
  echo "ERROR: --samples \"S1 S2 S3 ...\" is required" >&2
  exit 1
fi

if ! docker network ls --format '{{.Name}}' | grep -qx 'fabric_test'; then
  echo "ERROR: docker network 'fabric_test' not found — deploy the network first:" >&2
  echo "  ./startup.sh --orgs ${N} --skip-caliper" >&2
  exit 1
fi

CSV_DIR="${PROJECT_ROOT}/results/local"
mkdir -p "${CSV_DIR}"

FIRST=1
for S in ${SAMPLES}; do
  echo ""
  echo "========================================"
  echo "  Caliper matrix: orgs=${N} samples=${S}"
  echo "========================================"

  if [ "${FIRST}" -eq 1 ]; then
    echo ">> Generating CCP for ${N} orgs + benchmark config for ${S} samples..."
    bash "${GEN_CONFIG}" --orgs "${N}" --samples "${S}"
    FIRST=0
  else
    echo ">> Regenerating benchmark config for ${S} samples (CCP unchanged)..."
    bash "${GEN_CONFIG}" --orgs "${N}" --samples "${S}" --benchmark-only
  fi

  # Set per-run CSV output
  export CALIPER_RUN_CSV="${CSV_DIR}/caliper_org${N}_samples${S}.csv"

  echo ">> Running Caliper..."
  bash "${RUN_CALIPER}"

  echo ">> Results: ${CALIPER_RUN_CSV}"
done

echo ""
echo "========================================"
echo "  Caliper matrix complete"
echo "  Results in: ${CSV_DIR}/caliper_org${N}_samples*.csv"
echo "========================================"
