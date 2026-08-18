#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="${SCRIPT_DIR}/../../apps/ai_service/app/v1-0-0/api"
DB="${API_DIR}/offchain.db"
VENV="${SCRIPT_DIR}/../../../venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring"
REGISTER_ORGS=""

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -r) REGISTER_ORGS="1"; shift ;;
    *) break ;;
  esac
done

ORGS=("$@")
[ ${#ORGS[@]} -eq 0 ] && ORGS=(org1 org2 org3)
PYTHON="python3"
[ -x "${VENV}/bin/python" ] && PYTHON="${VENV}/bin/python"

mkdir -p "$(dirname "${DB}")"
echo ">> Writing API keys for: ${ORGS[*]}"
cd "${API_DIR}"

PYTHONPATH="${API_DIR}/../src" BOOTSTRAP_ORGS="${ORGS[*]}" "${PYTHON}" - <<'EOF'
import os
from storage import OffChainStore

s = OffChainStore("offchain.db")
for org in os.environ["BOOTSTRAP_ORGS"].split():
    s.upsert_org_key(f"key-{org}", org)
    print(f"  {org:5} -> key-{org}")

s.upsert_org_key("stress-key", "org1")
print("  stress-key -> org1 (demo/load driver)")

EOF

if [ -n "${REGISTER_ORGS}" ]; then

  echo ">> Registering orgs on the ledger..."

  "${SCRIPT_DIR}/register-orgs.sh" "${ORGS[@]}"
fi

echo
echo "Keys are ready. Start the gateway with:"
echo "  cd ${API_DIR}"
echo "  ${PYTHON} -m uvicorn server:app --host 0.0.0.0 --port 8000"
