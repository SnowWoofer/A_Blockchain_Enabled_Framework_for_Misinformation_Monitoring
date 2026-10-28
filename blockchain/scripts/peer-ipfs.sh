#!/usr/bin/env bash
# Wires the three IPFS nodes together, with node 0 as the dialer.

set -euo pipefail

DIALER="${DIALER:-ipfs-node}"
REPLICAS=(ipfs-node-1 ipfs-node-2)

peer_id() { docker exec "$1" ipfs id -f='<id>' 2>/dev/null | tr -d '\r'; }

entries=""
for r in "${REPLICAS[@]}"; do
  rid="$(peer_id "$r")"
  [ -z "${rid}" ] && { echo "ERROR: could not read peer id of ${r}" >&2; exit 1; }
  entries="${entries:+${entries},}{\"ID\":\"${rid}\",\"Addrs\":[\"/dns4/${r}/tcp/4001\"]}"
done

echo ">> Peering: ${DIALER} dials ${REPLICAS[*]}"
docker exec "${DIALER}" ipfs config --json Peering.Peers "[${entries}]" >/dev/null
# The replicas must NOT dial back: if a replica wins the dial race it owns the connection direction and the dialer can no longer serve over it.
for r in "${REPLICAS[@]}"; do
  docker exec "$r" ipfs config --json Peering.Peers '[]' >/dev/null
done

echo ">> Restarting (replicas first, dialer last so it initiates)..."
docker restart "${REPLICAS[@]}" >/dev/null
for r in "${REPLICAS[@]}"; do
  for _ in $(seq 1 45); do docker exec "$r" ipfs id >/dev/null 2>&1 && break; sleep 2; done
done
docker restart "${DIALER}" >/dev/null
for _ in $(seq 1 45); do docker exec "${DIALER}" ipfs id >/dev/null 2>&1 && break; sleep 2; done

sleep 12
echo ">> ${DIALER} outbound connections to replicas:"
for r in "${REPLICAS[@]}"; do
  rid="$(peer_id "$r")"
  n="$(docker exec "${DIALER}" ipfs swarm peers --direction 2>/dev/null | grep "${rid}" | grep -c outbound || true)"
  printf "   %-12s outbound: %s\n" "$r" "${n}"
done
