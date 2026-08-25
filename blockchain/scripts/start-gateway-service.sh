#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="${SCRIPT_DIR}/../../apps/fabric_gateway_service"
COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

if [[ "${1:-up}" == "down" ]]; then
  echo ">> Stopping Fabric Gateway SDK service..."
  (cd "${SERVICE_DIR}" && ${COMPOSE} down) >/dev/null 2>&1 || true
  exit 0
fi

if ! docker network ls --format '{{.Name}}' | grep -qx 'fabric_test'; then
  echo "ERROR: docker network 'fabric_test' not found — deploy the network first:" >&2
  echo "  ./startup.sh   or   blockchain/scripts/deploy.sh" >&2
  exit 1
fi

echo ">> Building + starting Fabric Gateway SDK service (official @hyperledger/fabric-gateway)..."
(cd "${SERVICE_DIR}" && ${COMPOSE} up -d --build) | tail -2

echo ">> Waiting for health on :9100..."
for _ in $(seq 1 60); do
  if curl -sf http://localhost:9100/health >/dev/null 2>&1; then
    echo ">> Gateway SDK service ready: http://localhost:9100"
    curl -s http://localhost:9100/health
    echo
    echo ">> Point the API at it:  export FABRIC_BACKEND=gateway FABRIC_GATEWAY_URL=http://localhost:9100"
    echo ">>                       (or leave FABRIC_BACKEND=auto — it prefers this service automatically)"
    exit 0
  fi
  sleep 2
done

echo "ERROR: gateway service did not become healthy on :9100" >&2
(cd "${SERVICE_DIR}" && ${COMPOSE} logs --tail 30)
exit 1
