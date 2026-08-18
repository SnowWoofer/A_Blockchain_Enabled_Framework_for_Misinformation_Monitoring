#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

OUT="${PROJECT_ROOT}/explorer/connection-profile/networkConfig.json"

N=3

while [ "$#" -gt 0 ]; do

  case "$1" in

    --orgs) shift; N="${1:-}" ;;

    *) echo "ERROR: unknown arg '$1'" >&2; exit 1 ;;
  esac

  shift
done

if ! [[ "${N}" =~ ^[0-9]+$ ]] || [ "${N}" -lt 1 ]; then
  echo "ERROR: --orgs must be a positive integer (got '${N}')" >&2
  exit 1
fi

mkdir -p "$(dirname "${OUT}")"

python3 - "${N}" > "${OUT}" <<'PY'
import json, sys
n = int(sys.argv[1])
base = "/tmp/crypto/peerOrganizations"
peers = []
organizations = {}
peer_entries = {}
for i in range(1, n + 1):
    org = f"org{i}"
    msp = f"Org{i}MSP"
    port = 7051 + 2000 * (i - 1)
    peer = f"peer0.{org}.example.com"
    peers.append(peer)
    organizations[msp] = {
        "mspid": msp,
        "adminPrivateKey": {
            "path": f"{base}/{org}.example.com/users/Admin@{org}.example.com/msp/keystore/priv_sk"
        },
        "peers": [peer],
        "signedCert": {
            "path": f"{base}/{org}.example.com/users/Admin@{org}.example.com/msp/signcerts/Admin@{org}.example.com-cert.pem"
        },
    }
    peer_entries[peer] = {
        "tlsCACerts": {"path": f"{base}/{org}.example.com/peers/{peer}/tls/ca.crt"},
        "url": f"grpcs://{peer}:{port}",
        "eventUrl": f"grpcs://{peer}:{port + 2}",
        "grpcOptions": {"ssl-target-name-override": peer},
    }

profile = {
    "name": "test-network",
    "version": "1.0.0",
    "license": "Apache-2.0",
    "client": {
        "tlsEnable": True,
        "adminCredential": {
            "id": "exploreradmin",
            "password": "exploreradminpw",
            "affiliation": "org1.department1",
        },
        "enableAuthentication": True,
        "organization": "Org1MSP",
        "connection": {"timeout": {"peer": {"endorser": "300"}, "orderer": "300"}},
    },
    "channels": {
        "mychannel": {
            "peers": {p: {} for p in peers},
            "connection": {
                "timeout": {
                    "peer": {"endorser": "6000", "eventHub": "6000", "eventReg": "6000"}
                }
            },
        }
    },
    "organizations": organizations,
    "peers": peer_entries,
    "orderers": {
        "orderer.example.com": {
            "url": "grpcs://orderer.example.com:7050",
            "tlsCACerts": {
                "path": "/tmp/crypto/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem"
            },
        }
    },
}
json.dump(profile, sys.stdout, indent=2)
print()

PY

echo ">> Explorer connection profile written for org1..${N}: ${OUT}"
