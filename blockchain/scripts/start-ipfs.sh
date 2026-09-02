#!/usr/bin/env bash
#
# Brings up the three-node IPFS swarm (apps/ipfs_gateway/docker-compose.yaml)
# and dials the nodes into one another.
#
# The nodes used to be a hand-run `docker run` container that this script tried
# to start with `docker compose up -d ipfs-node` — a service that did not exist
# in any compose file, so the call failed and the node only ever survived
# because someone had created it manually. They are compose services now.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

NODES=(ipfs-node ipfs-node-1 ipfs-node-2)
# node 0 keeps :5001 for backwards compatibility; 1 and 2 are offset by 100.
RPC_PORTS=(5001 5101 5201)

if [[ "${1:-up}" == "down" ]]; then
  echo ">> Stopping IPFS nodes..."
  (cd "${PROJECT_ROOT}" && ${COMPOSE} rm -sf "${NODES[@]}") >/dev/null 2>&1 || true
  exit 0
fi

echo ">> Starting IPFS swarm (${NODES[*]})..."
(cd "${PROJECT_ROOT}" && ${COMPOSE} up -d "${NODES[@]}") | tail -3

echo ">> Waiting for the RPC APIs..."
for port in "${RPC_PORTS[@]}"; do
  ready=""
  for _ in $(seq 1 60); do
    if curl -sf -X POST "http://localhost:${port}/api/v0/version" >/dev/null 2>&1; then
      ready="1"; break
    fi
    sleep 2
  done
  [ -z "${ready}" ] && { echo "ERROR: IPFS did not become ready on :${port}" >&2; exit 1; }
  echo "   ready: http://localhost:${port}"
done

"${SCRIPT_DIR}/peer-ipfs.sh"
