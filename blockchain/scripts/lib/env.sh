#!/usr/bin/env bash
# Sourced by the start-*.sh scripts: exports the repo-root .env (written by gen-secrets.sh) so `docker compose -f apps/<svc>/docker-compose.yaml`, whose project directory is not the repo root, still sees the shared secrets.


_env_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
_env_file="${SECRETS_ENV_FILE:-${_env_root}/.env}"

if [ ! -f "${_env_file}" ]; then
  "${_env_root}/blockchain/scripts/gen-secrets.sh"
fi

while IFS='=' read -r _k _v; do
  case "${_k}" in ''|\#*) continue ;; esac
  if [ -z "${!_k:-}" ]; then
    export "${_k}=${_v}"
  fi
done < "${_env_file}"

unset _env_root _env_file _k _v
