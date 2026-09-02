#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

if [[ "${1:-up}" == "down" ]]; then
  echo ">> Stopping blockchain gateway..."
  (cd "${PROJECT_ROOT}" && ${COMPOSE} rm -sf blockchain-gateway) >/dev/null 2>&1 || true
  exit 0
fi

echo ">> Building + starting blockchain gateway..."
(cd "${PROJECT_ROOT}" && ${COMPOSE} up -d --build blockchain-gateway) | tail -2

echo ">> Waiting for health on :8000..."
for _ in $(seq 1 30); do
  if curl -sf -H "X-API-Key: stress-key" http://localhost:8000/api/status >/dev/null 2>&1; then
    echo ">> blockchain gateway ready: http://localhost:8000"
    curl -s -H "X-API-Key: stress-key" http://localhost:8000/api/status
    echo
    exit 0
  fi
  sleep 1
done

echo "ERROR: blockchain gateway did not become healthy on :8000" >&2
echo "  (HTTP 401 here usually just means API keys aren't bootstrapped yet — run:" >&2
echo "   blockchain/scripts/bootstrap-keys.sh org1 org2 org3)" >&2
(cd "${PROJECT_ROOT}" && ${COMPOSE} logs --tail 30 blockchain-gateway)
exit 1
