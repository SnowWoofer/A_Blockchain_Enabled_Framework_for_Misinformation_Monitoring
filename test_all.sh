#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"
PYTHON="${MISINFO_PYTHON:-$PROJECT_ROOT/repair/.venv/bin/python}"
exec "$PYTHON" -m benchmarks.test_all "$@"
