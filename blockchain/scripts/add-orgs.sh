#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEST_NETWORK="${FABRIC_SAMPLES:-${PROJECT_ROOT}/fabric-samples}/test-network"
ADD_ORG3="${TEST_NETWORK}/addOrg3"
CHAINCODE_PATH="${PROJECT_ROOT}/chaincode/misinformation/go"

CC_NAME="misinformation"
CC_VERSION="2.2"
CHANNEL_NAME="mychannel"
TARGET=3

if ! command -v jq >/dev/null 2>&1 && [ -x "${HOME}/.local/bin/jq" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: 'jq' not found." >&2
  exit 1
fi
if docker compose version >/dev/null 2>&1; then
  export CONTAINER_CLI_COMPOSE="docker compose"
else
  export CONTAINER_CLI_COMPOSE="docker-compose"
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --orgs) shift; TARGET="${1:-}" ;;
    --channel) shift; CHANNEL_NAME="${1:-}" ;;
    --cc-name) shift; CC_NAME="${1:-}" ;;
    *) echo "ERROR: unknown arg '$1'" >&2; exit 1 ;;
  esac
  shift
done

if ! [[ "${TARGET}" =~ ^[0-9]+$ ]] || [ "${TARGET}" -lt 4 ]; then
  echo "ERROR: --orgs must be an integer >= 4 (got '${TARGET}')" >&2
  exit 1
fi

export PATH="${TEST_NETWORK}/../bin:${PATH}"
export FABRIC_CFG_PATH="${TEST_NETWORK}/../config"
export CORE_PEER_TLS_ENABLED=true
ORDERER_CA="${TEST_NETWORK}/organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem"

org_peer_port() { echo $((7051 + 2000 * ($1 - 1))); }

use_org() {
  local n="$1"
  local port
  port="$(org_peer_port "${n}")"
  export CORE_PEER_LOCALMSPID="Org${n}MSP"
  export CORE_PEER_TLS_ROOTCERT_FILE="${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/peers/peer0.org${n}.example.com/tls/ca.crt"
  export CORE_PEER_MSPCONFIGPATH="${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/users/Admin@org${n}.example.com/msp"
  export CORE_PEER_ADDRESS="localhost:${port}"
}

fetch_config() {
  use_org 1
  peer channel fetch config "${TEST_NETWORK}/channel-artifacts/config_block.pb" \
    -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
    -c "${CHANNEL_NAME}" --tls --cafile "${ORDERER_CA}" >/dev/null 2>&1
  configtxlator proto_decode --input "${TEST_NETWORK}/channel-artifacts/config_block.pb" \
    --type common.Block --output "${TEST_NETWORK}/channel-artifacts/config_block.json"
  jq '.data.data[0].payload.data.config' "${TEST_NETWORK}/channel-artifacts/config_block.json" \
    > "${TEST_NETWORK}/channel-artifacts/config.json"
}

emit_envelope() {
  local out="$1"
  configtxlator proto_encode --input "${TEST_NETWORK}/channel-artifacts/config.json" \
    --type common.Config --output "${TEST_NETWORK}/channel-artifacts/original_config.pb"
  configtxlator proto_encode --input "${TEST_NETWORK}/channel-artifacts/modified_config.json" \
    --type common.Config --output "${TEST_NETWORK}/channel-artifacts/modified_config.pb"
  configtxlator compute_update --channel_id "${CHANNEL_NAME}" \
    --original "${TEST_NETWORK}/channel-artifacts/original_config.pb" \
    --updated "${TEST_NETWORK}/channel-artifacts/modified_config.pb" \
    --output "${TEST_NETWORK}/channel-artifacts/config_update.pb"
  configtxlator proto_decode --input "${TEST_NETWORK}/channel-artifacts/config_update.pb" \
    --type common.ConfigUpdate --output "${TEST_NETWORK}/channel-artifacts/config_update.json"
  jq -n --arg ch "${CHANNEL_NAME}" --rawfile cu "${TEST_NETWORK}/channel-artifacts/config_update.json" \
    '{"payload":{"header":{"channel_header":{"channel_id":$ch,"type":2}},"data":{"config_update":($cu|fromjson)}}}' \
    > "${TEST_NETWORK}/channel-artifacts/config_update_in_envelope.json"
  configtxlator proto_encode --input "${TEST_NETWORK}/channel-artifacts/config_update_in_envelope.json" \
    --type common.Envelope --output "${out}"
}

sign_and_submit() {
  local tx="$1"
  local k="${2:-2}"
  for i in $(seq 1 "${k}"); do
    use_org "${i}"
    peer channel signconfigtx -f "${tx}" >/dev/null 2>&1 || true
  done
  use_org "${k}"
  peer channel update -f "${tx}" -c "${CHANNEL_NAME}" \
    -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
    --tls --cafile "${ORDERER_CA}" 2>&1 | tail -1
}

submit_as_org() {
  local tx="$1"
  local n="$2"
  use_org "${n}"
  peer channel update -f "${tx}" -c "${CHANNEL_NAME}" \
    -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
    --tls --cafile "${ORDERER_CA}" 2>&1 | tail -1
}

add_org_assets() {
  local n="$1"
  local p1 p2
  p1="$(org_peer_port "${n}")"
  p2=$((p1 + 1))
  echo ">> [org${n}] generating crypto material (cryptogen)..."
  local crypto_yaml="${ADD_ORG3}/crypto-org${n}.yaml"
  cat > "${crypto_yaml}" <<EOF
PeerOrgs:
  - Name: Org${n}
    Domain: org${n}.example.com
    EnableNodeOUs: true
    Template:
      Count: 1
      SANS:
        - localhost
    Users:
      Count: 1
EOF
  (cd "${TEST_NETWORK}" && cryptogen generate --config="${crypto_yaml}" --output="./organizations" >/dev/null 2>&1)
  rm -f "${crypto_yaml}"

  echo ">> [org${n}] generating org definition (configtxgen)..."


  local cfgdir="${ADD_ORG3}/.configtx-org${n}"
  mkdir -p "${cfgdir}"
  sed -e "s|MSPDir: ../organizations|MSPDir: ../../organizations|" \
    -e "s/Org3MSP/Org${n}MSP/g" -e "s/org3/org${n}/g" -e "s/Org3/Org${n}/g" \
    "${ADD_ORG3}/configtx.yaml" > "${cfgdir}/configtx.yaml"
  (cd "${cfgdir}" && FABRIC_CFG_PATH="${cfgdir}" configtxgen -printOrg "Org${n}MSP" \
    > "${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/org${n}.json" 2>/dev/null)
  rm -rf "${cfgdir}"

  echo ">> [org${n}] writing docker compose files..."
  cat > "${ADD_ORG3}/compose/compose-org${n}.yaml" <<EOF
volumes:
  peer0.org${n}.example.com:

networks:
  test:
    name: fabric_test

services:
  peer0.org${n}.example.com:
    container_name: peer0.org${n}.example.com
    image: hyperledger/fabric-peer:latest
    labels:
      service: hyperledger-fabric
    environment:
      - FABRIC_CFG_PATH=/etc/hyperledger/peercfg
      - FABRIC_LOGGING_SPEC=INFO
      - CORE_PEER_TLS_ENABLED=true
      - CORE_PEER_PROFILE_ENABLED=true
      - CORE_PEER_TLS_CERT_FILE=/etc/hyperledger/fabric/tls/server.crt
      - CORE_PEER_TLS_KEY_FILE=/etc/hyperledger/fabric/tls/server.key
      - CORE_PEER_TLS_ROOTCERT_FILE=/etc/hyperledger/fabric/tls/ca.crt
      - CORE_PEER_ID=peer0.org${n}.example.com
      - CORE_PEER_ADDRESS=peer0.org${n}.example.com:${p1}
      - CORE_PEER_MSPCONFIGPATH=/etc/hyperledger/fabric/msp
      - CORE_PEER_LISTENADDRESS=0.0.0.0:${p1}
      - CORE_PEER_CHAINCODEADDRESS=peer0.org${n}.example.com:${p2}
      - CORE_PEER_CHAINCODELISTENADDRESS=0.0.0.0:${p2}
      - CORE_PEER_GOSSIP_BOOTSTRAP=peer0.org${n}.example.com:${p1}
      - CORE_PEER_GOSSIP_EXTERNALENDPOINT=peer0.org${n}.example.com:${p1}
      - CORE_PEER_LOCALMSPID=Org${n}MSP
      - CORE_METRICS_PROVIDER=prometheus
      - CHAINCODE_AS_A_SERVICE_BUILDER_CONFIG={"peername":"peer0org${n}"}
      - CORE_CHAINCODE_EXECUTETIMEOUT=300s
    volumes:
      - ../../organizations/peerOrganizations/org${n}.example.com/peers/peer0.org${n}.example.com:/etc/hyperledger/fabric
      - peer0.org${n}.example.com:/var/hyperledger/production
    working_dir: /opt/gopath/src/github.com/hyperledger/fabric/peer
    command: peer node start
    ports:
      - ${p1}:${p1}
    networks:
      - test
EOF
  cat > "${ADD_ORG3}/compose/docker/docker-compose-org${n}.yaml" <<EOF
networks:
  test:
    name: fabric_test

services:
  peer0.org${n}.example.com:
    container_name: peer0.org${n}.example.com
    image: hyperledger/fabric-peer:latest
    labels:
      service: hyperledger-fabric
    environment:
      - CORE_VM_ENDPOINT=unix:///host/var/run/docker.sock
      - CORE_VM_DOCKER_HOSTCONFIG_NETWORKMODE=fabric_test
    volumes:
      - ./docker/peercfg:/etc/hyperledger/peercfg
      - \${DOCKER_SOCK}:/host/var/run/docker.sock
EOF
  echo ">> [org${n}] starting peer container (peer0.org${n}.example.com:${p1})..."
  local sock="${DOCKER_HOST:-/var/run/docker.sock}"
  local docker_sock="${sock##unix://}"
  (cd "${ADD_ORG3}" && DOCKER_SOCK="${docker_sock}" ${CONTAINER_CLI_COMPOSE} \
    -f compose/compose-org${n}.yaml -f compose/docker/docker-compose-org${n}.yaml up -d 2>&1 | tail -2)
}

add_org_to_channel() {
  local n="$1"
  echo ">> [org${n}] adding Org${n}MSP to channel '${CHANNEL_NAME}'..."
  fetch_config
  local exists
  exists="$(jq -r --arg msp "Org${n}MSP" \
    '.channel_group.groups.Application.groups[($msp)] != null' \
    "${TEST_NETWORK}/channel-artifacts/config.json")"
  if [ "${exists}" = "true" ]; then
    echo ">> [org${n}] Org${n}MSP already on channel — skipping config update."
  else
    jq -s --arg msp "Org${n}MSP" \
      '.[0] * {"channel_group":{"groups":{"Application":{"groups": {($msp): .[1]}}}}}' \
      "${TEST_NETWORK}/channel-artifacts/config.json" \
      "${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/org${n}.json" \
      > "${TEST_NETWORK}/channel-artifacts/modified_config.json"
    emit_envelope "${TEST_NETWORK}/channel-artifacts/org${n}_update_in_envelope.pb"
    sign_and_submit "${TEST_NETWORK}/channel-artifacts/org${n}_update_in_envelope.pb" "$((n - 1))"
  fi
  echo ">> [org${n}] joining peer0.org${n} to the channel..."
  use_org "${n}"
  local blockfile="${TEST_NETWORK}/channel-artifacts/${CHANNEL_NAME}.block"
  local rc=1 attempt=0 out=""
  while [ $rc -ne 0 ] && [ $attempt -lt 10 ]; do
    attempt=$((attempt + 1))
    peer channel fetch 0 "${blockfile}" -o localhost:7050 \
      --ordererTLSHostnameOverride orderer.example.com \
      -c "${CHANNEL_NAME}" --tls --cafile "${ORDERER_CA}" >/dev/null 2>&1 || { sleep 3; continue; }
    out="$(peer channel join -b "${blockfile}" 2>&1)" && rc=0
    if [ $rc -ne 0 ] && echo "${out}" | grep -qE "already belongs|already exists with state"; then
      echo ">> [org${n}] peer already joined — skipping."
      rc=0
    elif [ $rc -ne 0 ]; then
      sleep 3
    fi
  done
  if [ $rc -ne 0 ]; then
    echo "ERROR: peer0.org${n} failed to join the channel after retries: ${out}" >&2
    exit 1
  fi
  echo ">> [org${n}] peer0.org${n} joined the channel."
  echo ">> [org${n}] setting anchor peer peer0.org${n}:$(org_peer_port "${n}")..."
  fetch_config
  local anchored
  anchored="$(jq -r --arg msp "Org${n}MSP" \
    '.channel_group.groups.Application.groups[($msp)].values.AnchorPeers != null' \
    "${TEST_NETWORK}/channel-artifacts/config.json")"
  if [ "${anchored}" = "true" ]; then
    echo ">> [org${n}] anchor peer already set — skipping."
    return 0
  fi
  jq --arg msp "Org${n}MSP" --arg host "peer0.org${n}.example.com" --arg port "$(org_peer_port "${n}")" \
    '.channel_group.groups.Application.groups[$msp].values += {"AnchorPeers":{"mod_policy":"Admins","value":{"anchor_peers":[{"host":$host,"port":($port|tonumber)}]},"version":"0"}}' \
    "${TEST_NETWORK}/channel-artifacts/config.json" \
    > "${TEST_NETWORK}/channel-artifacts/modified_config.json"
  emit_envelope "${TEST_NETWORK}/channel-artifacts/org${n}_anchors.tx"
  submit_as_org "${TEST_NETWORK}/channel-artifacts/org${n}_anchors.tx" "${n}"
}

onboard_chaincode() {
  local total="$1"
  local policy="OutOf(2,"
  for n in $(seq 1 "${total}"); do policy+=" 'Org${n}MSP.member',"; done
  policy="${policy%,})"
  echo ">> Packaging chaincode ${CC_NAME} v${CC_VERSION}..."
  (cd "${CHAINCODE_PATH}" && go mod vendor)
  cd "${TEST_NETWORK}"
  peer lifecycle chaincode package misinformation-orgs.tar.gz \
    --path "${CHAINCODE_PATH}" --lang golang --label "${CC_NAME}_${CC_VERSION}" 2>&1 | tail -1
  local pkg
  pkg="$(peer lifecycle chaincode calculatepackageid misinformation-orgs.tar.gz)"
  echo ">> Package ID: ${pkg}"
  local cur_seq
  use_org 1
  cur_seq="$(peer lifecycle chaincode querycommitted --channelID "${CHANNEL_NAME}" \
    --name "${CC_NAME}" --output json 2>/dev/null \
    | python3 -c "import json,sys;print(json.load(sys.stdin).get('sequence',0))" || echo "0")"
  local seq=$((cur_seq + 1))
  echo ">> Re-committing at sequence ${seq} with policy ${policy}"
  for n in $(seq 1 "${total}"); do
    echo ">> Install on org${n}..."
    use_org "${n}"
    if ! out=$(peer lifecycle chaincode install misinformation-orgs.tar.gz 2>&1); then
      if ! echo "${out}" | grep -q "already successfully installed"; then
        echo "${out}" >&2
        exit 1
      fi
    fi
  done
  for n in $(seq 1 "${total}"); do
    echo ">> Approve on org${n}..."
    use_org "${n}"
    peer lifecycle chaincode approveformyorg -o localhost:7050 \
      --ordererTLSHostnameOverride orderer.example.com \
      --channelID "${CHANNEL_NAME}" --name "${CC_NAME}" \
      --version "${CC_VERSION}" --package-id "${pkg}" \
      --sequence "${seq}" --signature-policy "${policy}" \
      --tls --cafile "${ORDERER_CA}" --waitForEvent 2>&1 | tail -1
  done
  echo ">> Commit readiness..."
  use_org 1
  peer lifecycle chaincode checkcommitreadiness -o localhost:7050 \
    --ordererTLSHostnameOverride orderer.example.com \
    --channelID "${CHANNEL_NAME}" --name "${CC_NAME}" \
    --version "${CC_VERSION}" --sequence "${seq}" --signature-policy "${policy}" \
    --tls --cafile "${ORDERER_CA}" --output json 2>&1 | tail -1
  echo ">> Committing..."
  local commit_args=()
  for n in $(seq 1 "${total}"); do
    commit_args+=(--peerAddresses "localhost:$(org_peer_port "${n}")"
      --tlsRootCertFiles "${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/peers/peer0.org${n}.example.com/tls/ca.crt")
  done
  peer lifecycle chaincode commit -o localhost:7050 \
    --ordererTLSHostnameOverride orderer.example.com \
    --channelID "${CHANNEL_NAME}" --name "${CC_NAME}" \
    --version "${CC_VERSION}" --sequence "${seq}" --signature-policy "${policy}" \
    "${commit_args[@]}" --tls --cafile "${ORDERER_CA}" --waitForEvent 2>&1 | tail -2
  cd "${SCRIPT_DIR}"
}

if [ ! -d "${TEST_NETWORK}/organizations/peerOrganizations/org3.example.com" ]; then
  echo "ERROR: org3 crypto not found — run deploy.sh first (3-org baseline)." >&2
  exit 1
fi

for f in "${ADD_ORG3}"/compose/compose-org*.yaml; do
  [ -e "$f" ] || continue
  base="${f##*/}"
  n="${base#compose-org}"; n="${n%.yaml}"
  if [[ "${n}" =~ ^[0-9]+$ ]] && [ "${n}" -ge 4 ] && [ "${n}" -gt "${TARGET}" ]; then
    rm -f "${f}" "${ADD_ORG3}/compose/docker/docker-compose-org${n}.yaml"
  fi
done

echo ">> Adding peer orgs org4..org${TARGET} to the running network..."
for n in $(seq 4 "${TARGET}"); do
  if [ ! -d "${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com" ] || \
     [ ! -f "${ADD_ORG3}/compose/compose-org${n}.yaml" ]; then
    add_org_assets "${n}"
  else
    echo ">> [org${n}] crypto present, reusing (re-running after partial failure)."

    local_sock="${DOCKER_HOST:-/var/run/docker.sock}"
    (cd "${ADD_ORG3}" && DOCKER_SOCK="${local_sock##unix://}" ${CONTAINER_CLI_COMPOSE} \
      -f compose/compose-org${n}.yaml -f compose/docker/docker-compose-org${n}.yaml up -d 2>&1 | tail -2)
  fi
  add_org_to_channel "${n}"
done

onboard_chaincode "${TARGET}"

echo
echo "Done: channel '${CHANNEL_NAME}' now has ${TARGET} peer orgs (Org1MSP..Org${TARGET}MSP)."
echo "Next: regenerate the Explorer profile and recreate Explorer:"
echo "  ./scripts/gen-explorer-config.sh --orgs ${TARGET}"
