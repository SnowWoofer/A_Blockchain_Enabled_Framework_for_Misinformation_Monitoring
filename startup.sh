#!/usr/bin/env bash
# Current isolated v3 entry point. Existing deployments and volumes are retained.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PYTHON="${MISINFO_PYTHON:-python3}"
"$PYTHON" -c 'import yaml, cryptography, pydantic_settings, aiokafka' 2>/dev/null || { echo "Python environment is missing the project dependencies. Activate it first: source .venv/bin/activate (or set MISINFO_PYTHON)." >&2; exit 1; }
NAME=development
OPTIONS=()
OUTPUT=()
SAMPLES=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --name) NAME="${2:?missing experiment name}"; shift 2 ;;
    --orgs) OPTIONS+=(--orgs "${2:?missing organisation count}"); shift 2 ;;
    --database) OPTIONS+=(--database "${2:?missing database}"); shift 2 ;;
    --test-samples) SAMPLES="${2:?missing sample count}"; shift 2 ;;
    --verbose) OPTIONS+=(--verbose); OUTPUT+=(--verbose); shift ;;
    --skip-caliper) shift ;; # historical option: v3 never runs legacy Caliper
    --help)
      echo 'Usage: ./startup.sh [--name development] [--orgs N>=3] [--database leveldb|couchdb] [--verbose] [--test-samples N]'
      echo 'Starts an isolated v3 network and smoke test. Optional samples run SDK writes; see README.md.'
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$SAMPLES" =~ ^[0-9]+$ ]] || { echo '--test-samples must be nonnegative' >&2; exit 2; }
"$PYTHON" blockchain/scripts/deployment.py up --name "$NAME" "${OPTIONS[@]}"
"$PYTHON" blockchain/scripts/deployment.py smoke --name "$NAME" "${OUTPUT[@]}"
if [ "$SAMPLES" -gt 0 ]; then
  "$PYTHON" blockchain/scripts/deployment.py benchmark --name "$NAME" "${OUTPUT[@]}" --operation write --rate 25 --count "$SAMPLES"
fi
echo "Ready. Stop this deployment with: $PYTHON blockchain/scripts/deployment.py down --name $NAME"
