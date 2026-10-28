#!/usr/bin/env bash

# Generates every secret the stack needs, once, and never overwrites one that already exists (safe to re-run; called by startup.sh and demo.sh).
# Usage: gen-secrets.sh [--host NAME]...   extra DNS names for the certificate Overrides (tests): SECRETS_ENV_FILE, SECRETS_DIR

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${SECRETS_ENV_FILE:-${PROJECT_ROOT}/.env}"
SECRETS_DIR="${SECRETS_DIR:-${PROJECT_ROOT}/secrets}"
EXTRA_HOSTS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host) shift; EXTRA_HOSTS+=("${1:?--host needs a name}") ;;
    *) echo "ERROR: unknown arg '$1'" >&2; exit 1 ;;
  esac
  shift
done

command -v openssl >/dev/null 2>&1 || { echo "ERROR: openssl is required" >&2; exit 1; }
umask 077

touch "${ENV_FILE}"
chmod 600 "${ENV_FILE}"
added=0
ensure() {
  local name="$1" value="$2"
  if ! grep -q "^${name}=" "${ENV_FILE}"; then
    printf '%s=%s\n' "${name}" "${value}" >> "${ENV_FILE}"
    added=$((added + 1))
  fi
}
rand() { openssl rand -hex "${1:-32}"; }

ensure JWT_SECRET "$(rand 32)"
ensure INTERNAL_API_TOKEN "$(rand 32)"
ensure PSEUDONYM_KEY "$(rand 32)"
ensure PIPELINE_RAW_KEY "$(rand 32)"
ensure PIPELINE_FLAGGED_KEY "$(rand 32)"
ensure INGEST_API_KEY "$(rand 24)"
ensure SUBMITTER_API_KEY "$(rand 32)"
ensure GRAFANA_ADMIN_PASSWORD "$(rand 12)"
ensure HOST_UID "$(id -u)"
ensure HOST_GID "$(id -g)"
echo ">> ${ENV_FILE}: ${added} secret(s) generated, existing ones kept."

mkdir -p "${SECRETS_DIR}/tls"
chmod 700 "${SECRETS_DIR}"
if [ ! -s "${SECRETS_DIR}/tls/server.crt" ] || [ ! -s "${SECRETS_DIR}/tls/server.key" ]; then
  san="DNS:localhost,IP:127.0.0.1"
  for h in "${EXTRA_HOSTS[@]}"; do san="${san},DNS:${h}"; done
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 825 \
    -subj "/CN=localhost/O=Misinformation Monitor (self-signed)" \
    -addext "subjectAltName=${san}" \
    -addext "extendedKeyUsage=serverAuth" \
    -keyout "${SECRETS_DIR}/tls/server.key" -out "${SECRETS_DIR}/tls/server.crt" >/dev/null 2>&1
  chmod 600 "${SECRETS_DIR}/tls/server.key"
  chmod 644 "${SECRETS_DIR}/tls/server.crt"
  echo ">> Self-signed TLS certificate for ${san} -> ${SECRETS_DIR}/tls/"
  echo "   (browsers will warn until you trust it or replace it with a real certificate)"
else
  echo ">> TLS certificate already present in ${SECRETS_DIR}/tls/ (kept)."
fi
