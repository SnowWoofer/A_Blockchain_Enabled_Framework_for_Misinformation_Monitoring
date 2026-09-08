#!/usr/bin/env bash
# run-caliper.sh — benchmark the misinformation chaincode with Hyperledger Caliper.
#
#   run-caliper.sh            build + run the full benchmark suite, then show report path
#   run-caliper.sh down       remove the caliper container
#
# Runs inside the fabric_test docker network (same reason as the Explorer):
# discovered endorser hostnames resolve natively. Crypto material is mounted
# read-only at /crypto. Results land in benchmarks/caliper/report.html.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

if [[ "${1:-up}" == "down" ]]; then
  echo ">> Removing Caliper container..."
  (cd "${SCRIPT_DIR}" && ${COMPOSE} down) >/dev/null 2>&1 || true
  exit 0
fi

if ! docker network ls --format '{{.Name}}' | grep -qx 'fabric_test'; then
  echo "ERROR: docker network 'fabric_test' not found — deploy the network first:" >&2
  echo "  ./startup.sh   or   blockchain/scripts/deploy.sh" >&2
  exit 1
fi

echo ">> Building Caliper image (installs CLI + binds official Fabric Gateway SDK)..."
(cd "${SCRIPT_DIR}" && ${COMPOSE} build)

echo ">> Running benchmark suite: submit-report-write + query-all-reports-read"
cd "${SCRIPT_DIR}"
${COMPOSE} up --abort-on-container-exit --exit-code-from caliper
RC=$?
cd - >/dev/null
(cd "${SCRIPT_DIR}" && ${COMPOSE} down) >/dev/null 2>&1 || true

REPORT="${SCRIPT_DIR}/report.html"
if [[ -f "${REPORT}" ]]; then
  echo ""
  echo ">> Benchmark complete — HTML report: benchmarks/caliper/report.html"

  # --- extract summary table to CSV ---
  PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
  VENV="${PROJECT_ROOT}/venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring"
  PYTHON="python3"
  [ -x "${VENV}/bin/python" ] && PYTHON="${VENV}/bin/python"

  CSV_DIR="${PROJECT_ROOT}/results/local"
  CSV_PATH="${CSV_DIR}/caliper.csv"
  mkdir -p "${CSV_DIR}"

  "${PYTHON}" - "${REPORT}" "${CSV_PATH}" <<'PYEOF'
import sys, re, csv
from pathlib import Path
from datetime import datetime, timezone

html = Path(sys.argv[1]).read_text(encoding="utf-8")

m = re.search(r'<table[^>]*>\s*<h3>Summary of performance metrics(.*?)</table>', html, re.S)
if not m:
    print("  WARNING: could not find summary table in report.html", file=sys.stderr)
    sys.exit(0)

rows = re.findall(r'<tr>\s*(.*?)\s*</tr>', m.group(1), re.S)
if len(rows) < 2:
    print("  WARNING: summary table has no data rows", file=sys.stderr)
    sys.exit(0)

headers = [re.sub(r'<[^>]+>', '', c).strip().lower().replace(' ', '_').replace('(', '').replace(')', '') for c in re.findall(r'<th>(.*?)</th>', rows[0], re.S)]
fieldnames = ["timestamp"] + headers

csv_path = Path(sys.argv[2])
write_header = not csv_path.exists() or csv_path.stat().st_size == 0
ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

with open(csv_path, "a", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    if write_header:
        writer.writeheader()
    for row in rows[1:]:
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<td>(.*?)</td>', row, re.S)]
        if cells:
            record = {"timestamp": ts}
            for h, val in zip(headers, cells):
                try:
                    record[h] = float(val)
                except ValueError:
                    record[h] = val
            writer.writerow(record)

print(f"  Results: {sys.argv[2]}")
PYEOF

else
  echo ">> No report.html produced (see logs above)." >&2
fi
exit ${RC}
