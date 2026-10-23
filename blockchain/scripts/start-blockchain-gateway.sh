#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"
# Shared secrets (INTERNAL_API_TOKEN, JWT_SECRET, ...) from the repo-root .env
. "${SCRIPT_DIR}/lib/env.sh"

if [[ "${1:-up}" == "down" ]]; then
  echo ">> Stopping blockchain gateway..."
  (cd "${PROJECT_ROOT}" && ${COMPOSE} -f apps/blockchain_gateway/docker-compose.yaml rm -sf blockchain-gateway) >/dev/null 2>&1 || true
  exit 0
fi

echo ">> Building + starting blockchain gateway..."
(cd "${PROJECT_ROOT}" && ${COMPOSE} -f apps/blockchain_gateway/docker-compose.yaml up -d --build blockchain-gateway) | tail -2

echo ">> Waiting for health on :8000..."
for _ in $(seq 1 30); do
  if curl -sf http://localhost:8000/healthz >/dev/null 2>&1; then
    echo ">> blockchain gateway ready: http://localhost:8000"
    curl -s http://localhost:8000/api/public/info
    echo
    exit 0
  fi
  sleep 1
done

echo "ERROR: blockchain gateway did not become healthy on :8000" >&2
(cd "${PROJECT_ROOT}" && ${COMPOSE} -f apps/blockchain_gateway/docker-compose.yaml logs --tail 30 blockchain-gateway)
exit 1
