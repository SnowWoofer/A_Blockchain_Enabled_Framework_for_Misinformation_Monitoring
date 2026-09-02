#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

if [[ "${1:-up}" == "down" ]]; then
  echo ">> Stopping IPFS Gateway..."
  (cd "${PROJECT_ROOT}" && ${COMPOSE} rm -sf ipfs-gateway) >/dev/null 2>&1 || true
  exit 0
fi

echo ">> Building + starting IPFS Gateway..."
(cd "${PROJECT_ROOT}" && ${COMPOSE} up -d --build ipfs-gateway) | tail -2

echo ">> Waiting for health on :9101..."
for _ in $(seq 1 30); do
  if curl -sf http://localhost:9101/health >/dev/null 2>&1; then
    echo ">> IPFS Gateway ready: http://localhost:9101"
    curl -s http://localhost:9101/health
    echo
    exit 0
  fi
  sleep 1
done

echo "ERROR: IPFS Gateway did not become healthy on :9101" >&2
(cd "${PROJECT_ROOT}" && ${COMPOSE} logs --tail 30 ipfs-gateway)
exit 1
