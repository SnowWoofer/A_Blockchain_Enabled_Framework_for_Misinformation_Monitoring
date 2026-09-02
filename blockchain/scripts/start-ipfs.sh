#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

if [[ "${1:-up}" == "down" ]]; then
  echo ">> Stopping IPFS..."
  (cd "${PROJECT_ROOT}" && ${COMPOSE} rm -sf ipfs-node) >/dev/null 2>&1 || true
  exit 0
fi

echo ">> Starting IPFS (ipfs-node)..."
(cd "${PROJECT_ROOT}" && ${COMPOSE} up -d ipfs-node) | tail -2

echo ">> Waiting for the RPC API on :5001..."
for _ in $(seq 1 60); do
  if curl -sf -X POST http://localhost:5001/api/v0/version >/dev/null 2>&1; then
    echo ">> IPFS ready: http://localhost:5001"
    exit 0
  fi
  sleep 2
done

echo "ERROR: IPFS did not become ready on :5001" >&2
exit 1
