#!/usr/bin/env bash
#
# Register role-bearing identities for each org via Fabric CA.
#
# For each org N (1..LIMIT), this script registers and enrolls:
#   - officialN   (role=official)   — admin/governance operations
#   - factcheckerN (role=fact_checker) — submit + fact-check operations
#
# All orgs use Fabric CA (org1-3 from network.sh, org4+ from add-orgs.sh).
# CA ports: org1=7054, org2=8054, org3=11054, org4+=12054+
#
# Usage:  ./register-roles.sh [--limit 5]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEST_NETWORK="${FABRIC_SAMPLES:-${PROJECT_ROOT}/fabric-samples}/test-network"
LIMIT=3

while [ "$#" -gt 0 ]; do
  case "$1" in
    --limit) shift; LIMIT="${1:-}" ;;
    *) echo "ERROR: unknown arg '$1'" >&2; exit 1 ;;
  esac
  shift
done

if ! [[ "${LIMIT}" =~ ^[0-9]+$ ]] || [ "${LIMIT}" -lt 1 ]; then
  echo "ERROR: --limit must be a positive integer, got '${LIMIT}'" >&2
  exit 1
fi

# CA ports: org1=7054, org2=8054, org3=11054, org4+=12054,13054,...
ca_port() {
  case "$1" in
    1) echo 7054 ;;
    2) echo 8054 ;;
    3) echo 11054 ;;
    *) if [ "$1" -ge 4 ]; then echo $((12054 + 1000 * ($1 - 4))); else echo ""; fi ;;
  esac
}

# CA TLS cert path
ca_cert() {
  local n="$1"
  local standard="${TEST_NETWORK}/organizations/fabric-ca/org${n}/ca-cert.pem"
  local addorg3="${TEST_NETWORK}/addOrg3/fabric-ca/org${n}/ca-cert.pem"
  if [ -f "${standard}" ]; then
    echo "${standard}"
  elif [ -f "${addorg3}" ]; then
    echo "${addorg3}"
  else
    echo ""
  fi
}

# Fabric CA client binary
FABRIC_CA_CLIENT="${TEST_NETWORK}/../bin/fabric-ca-client"
if ! command -v fabric-ca-client >/dev/null 2>&1 && [ -x "${FABRIC_CA_CLIENT}" ]; then
  export PATH="${TEST_NETWORK}/../bin:${PATH}"
fi
if ! command -v fabric-ca-client >/dev/null 2>&1; then
  echo "ERROR: fabric-ca-client not found. Run 'make fabric-ca-client' in fabric-samples." >&2
  exit 1
fi

export FABRIC_CFG_PATH="${TEST_NETWORK}/../config"

enroll_ca_admin() {
  local n="$1" port="$2" cert="$3"
  local org_root="${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com"

  # Must set FABRIC_CA_CLIENT_HOME BEFORE the skip check so that
  # subsequent fabric-ca-client register calls can find the enrollment.
  export FABRIC_CA_CLIENT_HOME="${org_root}"

  if [ -d "${org_root}/msp/keystore" ]; then
    echo ">> [org${n}] CA admin already enrolled — skipping."
    return 0
  fi

  echo ">> [org${n}] enrolling CA admin (admin:adminpw)..."
  mkdir -p "${org_root}"
  fabric-ca-client enroll \
    -u "https://admin:adminpw@localhost:${port}" \
    --caname "ca-org${n}" \
    -M "${org_root}/users/Admin@org${n}.example.com/msp" \
    --tls.certfiles "${cert}" 2>&1 | tail -1
}

register_and_enroll() {
  local n="$1" role="$2" port="$3" cert="$4"
  local name="${role}${n}"
  local secret="${name}pw"
  local msp_dir="${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/users/${name}@org${n}.example.com/msp"

  if [ -d "${msp_dir}/signcerts" ]; then
    echo ">> [org${n}] ${name} already enrolled — skipping."
    return 0
  fi

  echo ">> [org${n}] registering ${name} (role=${role})..."
  fabric-ca-client register \
    --caname "ca-org${n}" \
    --id.name "${name}" \
    --id.secret "${secret}" \
    --id.type client \
    --id.attrs "role=${role}:ecert" \
    --tls.certfiles "${cert}" 2>&1 | tail -1 || true

  echo ">> [org${n}] enrolling ${name}..."
  mkdir -p "${msp_dir}"
  fabric-ca-client enroll \
    -u "https://${name}:${secret}@localhost:${port}" \
    --caname "ca-org${n}" \
    -M "${msp_dir}" \
    --tls.certfiles "${cert}" 2>&1 | tail -1

  # Copy NodeOUs config
  local org_msp="${TEST_NETWORK}/organizations/peerOrganizations/org${n}.example.com/msp"
  if [ -f "${org_msp}/config.yaml" ]; then
    cp "${org_msp}/config.yaml" "${msp_dir}/config.yaml"
  fi
  echo ">> [org${n}] ${name} enrolled → ${msp_dir}"
}

echo ">> Registering role-bearing identities for orgs 1..${LIMIT}..."
for n in $(seq 1 "${LIMIT}"); do
  port="$(ca_port "${n}")"
  cert="$(ca_cert "${n}")"
  if [ -z "${port}" ] || [ -z "${cert}" ]; then
    echo ">> [org${n}] no CA available (cryptogen org) — skipping role registration."
    continue
  fi
  if [ ! -f "${cert}" ]; then
    echo ">> [org${n}] CA cert not found at ${cert} — skipping."
    continue
  fi
  enroll_ca_admin "${n}" "${port}" "${cert}"
  register_and_enroll "${n}" "official" "${port}" "${cert}"
  register_and_enroll "${n}" "fact_checker" "${port}" "${cert}"
done

unset FABRIC_CA_CLIENT_HOME

echo
echo ">> Role registration complete."
echo "   officialN@orgN.example.com   — role=official  (admin/governance)"
echo "   factcheckerN@orgN.example.com — role=fact_checker (submit/fact-check)"
echo "   Admin@orgN.example.com        — no role attribute (query-only)"
