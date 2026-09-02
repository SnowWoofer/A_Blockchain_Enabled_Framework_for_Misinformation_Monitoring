#!/usr/bin/env bash
#
# Adds a CLIENT-ONLY organisation to the channel: an MSP identity with no peer,
# no ledger copy and no infrastructure of any kind.
#
# Why this exists: most credible fact-checking organisations — newsrooms,
# universities, civil-society groups — cannot fund a 24/7 blockchain node. They
# can still be full voting members of the consortium. A member needs ~44KB of
# crypto material to sign with; endorsement is supplied by whichever orgs do run
# peers (the policy is OutOf(2,...), satisfied by the founding three).
#
# What a client-only org CAN do:
#   - sign and submit transactions; its votes are recorded under its own MSP,
#     and no peer can forge or alter them
#   - independently verify any claim (see thin-verifier.py)
# What it CANNOT do:
#   - endorse. The org definition's Endorsement policy is OR('OrgNMSP.peer'),
#     which an org with no peers can never satisfy. This is correct, not a
#     limitation to work around.
#   - hold its own ledger replica, so it must read through someone's peer.
#
# Usage:  ./add-client-org.sh [--org 4] [--channel mychannel]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEST_NETWORK="${FABRIC_SAMPLES:-${PROJECT_ROOT}/fabric-samples}/test-network"
ADD_ORG3="${TEST_NETWORK}/addOrg3"
CHANNEL_NAME="mychannel"
N=4

if ! command -v jq >/dev/null 2>&1 && [ -x "${HOME}/.local/bin/jq" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi
command -v jq >/dev/null 2>&1 || { echo "ERROR: 'jq' not found." >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --org) shift; N="${1:-}" ;;
    --channel) shift; CHANNEL_NAME="${1:-}" ;;
    *) echo "ERROR: unknown arg '$1'" >&2; exit 1 ;;
  esac
  shift
done
[[ "${N}" =~ ^[0-9]+$ ]] && [ "${N}" -ge 4 ] || { echo "ERROR: --org must be >= 4" >&2; exit 1; }

export PATH="${TEST_NETWORK}/../bin:${PATH}"
export FABRIC_CFG_PATH="${TEST_NETWORK}/../config"
export CORE_PEER_TLS_ENABLED=true
ORDERER_CA="${TEST_NETWORK}/organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem"
ORGDIR="${TEST_NETWORK}/organizations/peerOrganizations/org${N}.example.com"
ARTIFACTS="${TEST_NETWORK}/channel-artifacts"

use_org() {
  local n="$1"
  export CORE_PEER_LOCALMSPID="Org${n}MSP"
  export CORE_PEER_TLS_ROOTCERT_FILE="${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/peers/peer0.org${n}.example.com/tls/ca.crt"
  export CORE_PEER_MSPCONFIGPATH="${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/users/Admin@org${n}.example.com/msp"
  export CORE_PEER_ADDRESS="localhost:$((7051 + 2000 * (n - 1)))"
}

# ---------------------------------------------------------------- crypto ----
if [ -d "${ORGDIR}/users/Admin@org${N}.example.com/msp" ]; then
  echo ">> [org${N}] crypto material already present — skipping cryptogen."
else
  echo ">> [org${N}] generating crypto material (NO peers)..."
  crypto_yaml="${ADD_ORG3}/crypto-client-org${N}.yaml"
  cat > "${crypto_yaml}" <<EOF
PeerOrgs:
  - Name: Org${N}
    Domain: org${N}.example.com
    EnableNodeOUs: true
    Template:
      Count: 0
    Users:
      Count: 1
EOF
  (cd "${TEST_NETWORK}" && cryptogen generate --config="${crypto_yaml}" --output="./organizations" >/dev/null)
  rm -f "${crypto_yaml}"
fi
# No peers/ directory is the expected outcome here — count defensively, since
# `ls` on a missing path fails and `set -o pipefail` would abort the script.
peers_made=0
[ -d "${ORGDIR}/peers" ] && peers_made="$(find "${ORGDIR}/peers" -mindepth 1 -maxdepth 1 | wc -l)"
echo ">> [org${N}] peers provisioned: ${peers_made} (expected 0)"

# ------------------------------------------------------- org definition -----
echo ">> [org${N}] generating org definition (configtxgen)..."
cfgdir="${ADD_ORG3}/.configtx-client-org${N}"
mkdir -p "${cfgdir}"
sed -e "s|MSPDir: ../organizations|MSPDir: ../../organizations|" \
    -e "s/Org3MSP/Org${N}MSP/g" -e "s/org3/org${N}/g" -e "s/Org3/Org${N}/g" \
    "${ADD_ORG3}/configtx.yaml" > "${cfgdir}/configtx.yaml"
(cd "${cfgdir}" && FABRIC_CFG_PATH="${cfgdir}" configtxgen -printOrg "Org${N}MSP" \
   > "${ORGDIR}/org${N}.json" 2>/dev/null)
rm -rf "${cfgdir}"
[ -s "${ORGDIR}/org${N}.json" ] || { echo "ERROR: empty org definition" >&2; exit 1; }

# ------------------------------------------------- channel config update ----
echo ">> [org${N}] adding Org${N}MSP to channel '${CHANNEL_NAME}'..."
use_org 1
peer channel fetch config "${ARTIFACTS}/config_block.pb" \
  -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
  -c "${CHANNEL_NAME}" --tls --cafile "${ORDERER_CA}" >/dev/null 2>&1
configtxlator proto_decode --input "${ARTIFACTS}/config_block.pb" \
  --type common.Block --output "${ARTIFACTS}/config_block.json"
jq '.data.data[0].payload.data.config' "${ARTIFACTS}/config_block.json" > "${ARTIFACTS}/config.json"

if [ "$(jq -r --arg msp "Org${N}MSP" '.channel_group.groups.Application.groups[($msp)] != null' "${ARTIFACTS}/config.json")" = "true" ]; then
  echo ">> [org${N}] Org${N}MSP already on the channel — skipping config update."
else
  jq -s --arg msp "Org${N}MSP" \
    '.[0] * {"channel_group":{"groups":{"Application":{"groups": {($msp): .[1]}}}}}' \
    "${ARTIFACTS}/config.json" "${ORGDIR}/org${N}.json" > "${ARTIFACTS}/modified_config.json"

  configtxlator proto_encode --input "${ARTIFACTS}/config.json" --type common.Config --output "${ARTIFACTS}/original_config.pb"
  configtxlator proto_encode --input "${ARTIFACTS}/modified_config.json" --type common.Config --output "${ARTIFACTS}/modified_config.pb"
  configtxlator compute_update --channel_id "${CHANNEL_NAME}" \
    --original "${ARTIFACTS}/original_config.pb" --updated "${ARTIFACTS}/modified_config.pb" \
    --output "${ARTIFACTS}/config_update.pb"
  configtxlator proto_decode --input "${ARTIFACTS}/config_update.pb" \
    --type common.ConfigUpdate --output "${ARTIFACTS}/config_update.json"
  jq -n --arg ch "${CHANNEL_NAME}" --rawfile cu "${ARTIFACTS}/config_update.json" \
    '{"payload":{"header":{"channel_header":{"channel_id":$ch,"type":2}},"data":{"config_update":($cu|fromjson)}}}' \
    > "${ARTIFACTS}/config_update_in_envelope.json"
  TX="${ARTIFACTS}/client_org${N}_update.pb"
  configtxlator proto_encode --input "${ARTIFACTS}/config_update_in_envelope.json" --type common.Envelope --output "${TX}"

  # The Application group's mod_policy is MAJORITY Admins, so the update needs
  # signatures from a majority of the existing member orgs.
  for i in 1 2; do use_org "${i}"; peer channel signconfigtx -f "${TX}" >/dev/null 2>&1 || true; done
  use_org 3
  peer channel update -f "${TX}" -c "${CHANNEL_NAME}" \
    -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
    --tls --cafile "${ORDERER_CA}" 2>&1 | tail -1
fi

echo
echo ">> org${N} is now a channel member with NO peer."
echo "   MSP:      Org${N}MSP"
echo "   identity: ${ORGDIR}/users/Admin@org${N}.example.com/msp"
echo "   size:     $(du -sh "${ORGDIR}/users/Admin@org${N}.example.com/msp" 2>/dev/null | cut -f1)"
echo
echo "   Next: it must still be admitted by consortium vote before it can"
echo "   fact-check (the founding-org slots are closed):"
echo "     POST /api/orgs/apply                        (as org${N})"
echo "     POST /api/orgs/Org${N}MSP/admission/vote     (existing orgs, quorum 2)"
echo "     POST /api/orgs/Org${N}MSP/admission/finalize"
