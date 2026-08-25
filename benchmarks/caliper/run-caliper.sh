#!/usr/bin/env bash
# run-caliper.sh — benchmark the misinformation chaincode with Hyperledger Caliper.
#
#   run-caliper.sh            build + run the full benchmark suite, then show report path
#   run-caliper.sh down       remove the caliper container
#
# Runs inside the fabric_test docker network (same reason as the Explorer):
# discovered endorser hostnames resolve natively. Crypto material is mounted
# read-only at /crypto. Results land in benchmarks/caliper/report.html.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

if [[ "${1:-up}" == "down" ]]; then
  echo ">> Removing Caliper container..."
  (cd "${SCRIPT_DIR}" && ${COMPOSE} down) >/dev/null 2>&1 || true
  exit 0
fi

if ! docker network ls --format '{{.Name}}' | grep -qx 'fabric_test'; then
  echo "ERROR: docker network 'fabric_test' not found — deploy the network first:" >&2
  echo "  ./startup.sh   or   blockchain/scripts/deploy.sh" >&2
  exit 1
fi

echo ">> Building Caliper image (installs CLI + binds official Fabric Gateway SDK)..."
(cd "${SCRIPT_DIR}" && ${COMPOSE} build)

echo ">> Running benchmark suite: submit-report-write + query-all-reports-read"
cd "${SCRIPT_DIR}"
${COMPOSE} up --abort-on-container-exit --exit-code-from caliper
RC=$?
cd - >/dev/null
(cd "${SCRIPT_DIR}" && ${COMPOSE} down) >/dev/null 2>&1 || true

REPORT="${SCRIPT_DIR}/report.html"
if [[ -f "${REPORT}" ]]; then
  echo ""
  echo ">> Benchmark complete — HTML report: benchmarks/caliper/report.html"
else
  echo ">> No report.html produced (see logs above)." >&2
fi
exit ${RC}
