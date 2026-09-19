#!/usr/bin/env bash
# Turns the three IPFS nodes into a PRIVATE swarm (libp2p pnet).
#
# Why: these nodes hold the consortium's claim documents. On the public IPFS
# network every one of those documents is announced to the global DHT and
# fetchable by anyone who learns the CID — which is wrong for this system, and
# was also breaking it in practice: node 0 publishes :4001 to the host, so it
# accumulated hundreds of inbound public peers and starved its own siblings'
# bitswap streams ("remote sent go away"), making cross-node retrieval hang.
#
# A shared swarm.key fixes both. libp2p refuses to speak to any peer that does
# not hold the same key, so the three nodes form a closed network: no public
# announcement, no public traffic, and sibling connections stay healthy.
#
# The key is generated once and reused; delete blockchain/scripts/swarm.key and
# re-run to rotate it (every node must be restarted together after a rotation).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_FILE="${SCRIPT_DIR}/swarm.key"
NODES=(ipfs-node ipfs-node-1 ipfs-node-2)

if [ ! -f "${KEY_FILE}" ]; then
  echo ">> Generating a new swarm key..."
  {
    echo "/key/swarm/psk/1.0.0/"
    echo "/base16/"
    od -A none -t x1 -N 32 /dev/urandom | tr -d ' \n'
    echo
  } > "${KEY_FILE}"
  chmod 600 "${KEY_FILE}"
fi

echo ">> Installing swarm key + clearing public bootstrap on ${#NODES[@]} nodes..."
for n in "${NODES[@]}"; do
  docker cp "${KEY_FILE}" "${n}:/data/ipfs/swarm.key" >/dev/null
  # docker cp lands the file as root; kubo runs as uid 1000 and refuses to
  # start if it cannot read its own repo files.
  docker exec -u 0 "${n}" chown 1000:100 /data/ipfs/swarm.key >/dev/null 2>&1 || true
  # kubo >=0.43 aborts on a private network unless AutoConf is off: it would
  # otherwise try to fetch the public mainnet config it must not use here.
  docker exec "${n}" ipfs config --json AutoConf.Enabled false >/dev/null 2>&1 || true
  docker exec "${n}" ipfs config --json Routing.DelegatedRouters '[]' >/dev/null 2>&1 || true
  # Public bootstrap peers cannot be reached inside a private swarm anyway;
  # leaving them in just produces a stream of failed dials.
  docker exec "${n}" ipfs bootstrap rm --all >/dev/null 2>&1 || true
  echo "   ${n}"
done

echo ">> Restarting nodes..."
docker restart "${NODES[@]}" >/dev/null
for n in "${NODES[@]}"; do
  for _ in $(seq 1 45); do
    docker exec "$n" ipfs id >/dev/null 2>&1 && break
    sleep 2
  done
done

sleep 6
echo ">> Swarm now contains only the consortium's own nodes:"
for n in "${NODES[@]}"; do
  printf "   %-12s peers: %s\n" "${n}" "$(docker exec "${n}" ipfs swarm peers 2>/dev/null | wc -l)"
done
