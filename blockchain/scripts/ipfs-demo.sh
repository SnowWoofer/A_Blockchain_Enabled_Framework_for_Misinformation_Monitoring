#!/usr/bin/env bash
#
# Demonstrates, against the running three-node swarm, the four IPFS properties
# this project actually depends on — and which a conventional object store or
# database could not provide.
#
#   ./blockchain/scripts/ipfs-demo.sh [<cid>]
#
# With no argument it uses the newest claim anchored on the ledger.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPERS="${SCRIPT_DIR}/lib/ipfs_demo_helpers.py"
API_KEY="${API_KEY:-stress-key}"
BASE="${BASE:-http://localhost:8000}"
NODE0=ipfs-node
NODE1=ipfs-node-1
NODE2=ipfs-node-2
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

CID="${1:-}"
if [ -z "${CID}" ]; then
  CID="$(curl -s -m 20 "${BASE}/api/reports" -H "X-API-Key: ${API_KEY}" | python3 "${HELPERS}" newest)"
fi
[ -z "${CID}" ] && { echo "ERROR: no report on the ledger to demonstrate with" >&2; exit 1; }

hr() { printf '%.0s-' $(seq 1 74); echo; }
only_hash() { docker exec -i "$1" ipfs add -Q --only-hash 2>/dev/null | tr -d '\r'; }

hr
echo "Claim anchored on the ledger:  ${CID}"
hr

echo
echo "[1] CONTENT ADDRESSING - the id IS the hash of the bytes"
docker exec "${NODE0}" ipfs cat "${CID}" > "${WORK}/claim.json" 2>/dev/null
RECOMPUTED="$(only_hash "${NODE0}" < "${WORK}/claim.json")"
echo "    ledger anchored : ${CID}"
echo "    recomputed      : ${RECOMPUTED}"
if [ "${CID}" = "${RECOMPUTED}" ]; then
  echo "    -> MATCH. Nothing had to trust a storage server's word for it."
else
  echo "    -> MISMATCH (unexpected)"
fi

echo
echo "[2] PEER-TO-PEER RETRIEVAL - nodes 1 and 2 were never sent this document"
for n in "${NODE1}" "${NODE2}"; do
  if docker exec "${n}" ipfs block stat --offline "${CID}" >/dev/null 2>&1; then
    printf "    %-12s before  : already cached locally\n" "${n}"
  else
    printf "    %-12s before  : does NOT hold this block\n" "${n}"
  fi
done
for n in "${NODE1}" "${NODE2}"; do
  GOT="$(docker exec "${n}" ipfs cat "${CID}" 2>/dev/null | python3 "${HELPERS}" text)"
  printf "    %-12s fetched : %s\n" "${n}" "${GOT}"
done
echo "    -> No copy step, no replication setting, no primary node."

echo
echo "[3] DEDUPLICATION - identical bytes yield the identical id, everywhere"
D1="$(only_hash "${NODE1}" < "${WORK}/claim.json")"
D2="$(only_hash "${NODE2}" < "${WORK}/claim.json")"
echo "    node-1 computes : ${D1}"
echo "    node-2 computes : ${D2}"
if [ "${D1}" = "${D2}" ] && [ "${D1}" = "${CID}" ]; then
  echo "    -> Same id, three independent nodes. Re-submitting stores nothing new."
else
  echo "    -> MISMATCH (unexpected)"
fi

echo
echo "[4] TAMPER-EVIDENCE - the property the verification design rests on"
python3 "${HELPERS}" tamper < "${WORK}/claim.json" > "${WORK}/tampered.json"
TAMPERED="$(only_hash "${NODE0}" < "${WORK}/tampered.json")"
echo "    original        : ${CID}"
echo "    verdict flipped : ${TAMPERED}"
if [ "${CID}" != "${TAMPERED}" ]; then
  echo "    -> Different id. A forged document cannot be served under the id the"
  echo "       ledger anchored, so the substitution is detectable by anyone."
fi
echo
hr
