#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
PYTHON="${MISINFO_PYTHON:-python3}"
"$PYTHON" -c 'import yaml, cryptography, pydantic_settings, aiokafka' 2>/dev/null || { echo "Python environment is missing the project dependencies. Activate it first: source .venv/bin/activate (or set MISINFO_PYTHON)." >&2; exit 1; }
exec "$PYTHON" blockchain/explorer/manage.py "$@"
