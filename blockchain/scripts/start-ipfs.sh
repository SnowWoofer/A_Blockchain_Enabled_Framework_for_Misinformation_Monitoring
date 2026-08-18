#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${IPFS_CONTAINER:-ipfs-node}"
IMAGE="${IPFS_IMAGE:-ipfs/kubo:latest}"

if [[ "${1:-up}" == "down" ]]; then
  echo ">> Stopping IPFS container ${CONTAINER}..."
  docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  exit 0
fi

if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  echo ">> IPFS node already running (${CONTAINER})."
  exit 0
fi

if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  echo ">> Starting existing IPFS container ${CONTAINER}..."
  docker start "${CONTAINER}"
else
  echo ">> Starting new IPFS node ${CONTAINER} (${IMAGE})..."
  docker run -d --name "${CONTAINER}" \
    -v ipfs-data:/data/ipfs \
    -p 5001:5001 \
    -p 8081:8080 \
    -p 4001:4001 \
    -p 4001:4001/udp \
    --restart unless-stopped \
    "${IMAGE}"
fi

echo ">> Waiting for the RPC API on :5001..."

for _ in $(seq 1 90); do

  if curl -sf http://localhost:5001/api/v0/version >/dev/null 2>&1; then

    echo ">> IPFS ready: http://localhost:5001"

    exit 0
  fi

  sleep 2
done

echo "ERROR: IPFS did not become ready on :5001" >&2

exit 1
