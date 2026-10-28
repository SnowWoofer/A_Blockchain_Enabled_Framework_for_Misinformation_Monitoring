#!/usr/bin/env bash

# Seeds the blockchain gateway's API keys (stored hashed in offchain.db).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="${SCRIPT_DIR}/../../apps/blockchain_gateway/app/v1-0-0/api"
DB="${API_DIR}/offchain.db"
VENV="${SCRIPT_DIR}/../../../venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring"
REGISTER_ORGS=""
DEV_KEYS="1"

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -r) REGISTER_ORGS="1"; shift ;;
    --no-dev-keys) DEV_KEYS=""; shift ;;
    *) break ;;
  esac
done

ORGS=("$@")
[ ${#ORGS[@]} -eq 0 ] && ORGS=(org1 org2 org3)
PYTHON="python3"
[ -x "${VENV}/bin/python" ] && PYTHON="${VENV}/bin/python"

. "${SCRIPT_DIR}/lib/env.sh"

mkdir -p "$(dirname "${DB}")"
echo ">> Writing API keys for: ${ORGS[*]}"
cd "${API_DIR}"

PYTHONPATH="${API_DIR}/../src" BOOTSTRAP_ORGS="${ORGS[*]}" DEV_KEYS="${DEV_KEYS}" \
  AI_ORG="${AI_STAKEHOLDER_ORG:-org1}" IPFS_GATEWAY_URL="http://127.0.0.1:1" "${PYTHON}" - <<'EOF'
import os
from storage import OffChainStore

s = OffChainStore("offchain.db")
orgs = os.environ["BOOTSTRAP_ORGS"].split()

if os.environ["DEV_KEYS"]:
    for org in orgs:
        s.upsert_org_key(f"key-{org}", org, role="official", label=f"dev:{org}")
        print(f"  {org:5} -> key-{org} (official, well-known benchmark key)")
    s.upsert_org_key("stress-key", "org1", role="official", label="dev:stress")
    print("  stress-key -> org1 (demo/load driver)")
else:
    well_known = [f"key-org{i}" for i in range(1, 31)] + ["stress-key"]
    revoked = sum(s.revoke_keys(api_key=k) for k in well_known)
    print(f"  well-known benchmark keys not issued ({revoked} left-over key(s) revoked)")

submitter = os.environ.get("SUBMITTER_API_KEY", "")
if submitter:
    s.upsert_org_key(submitter, os.environ["AI_ORG"], role="ai_submitter", label="submission-worker")
    print(f"  SUBMITTER_API_KEY -> {os.environ['AI_ORG']} (ai_submitter: POST /api/reports only)")
EOF
chmod 600 "${DB}" 2>/dev/null || true

if [ -n "${REGISTER_ORGS}" ]; then

  echo ">> Registering orgs on the ledger..."

  "${SCRIPT_DIR}/register-orgs.sh" --limit "${#ORGS[@]}"
fi

echo
echo "Keys are ready. Start the gateway with:"
echo "  blockchain/scripts/start-blockchain-gateway.sh"
