#!/usr/bin/env bash
# Exports Main.dc.html to an A1 landscape PDF (841 x 594 mm) with headless
# Chrome or Chromium. Re-run after editing the poster.
#
#   ./export-pdf.sh                  -> misinformation-monitoring-poster-A1.pdf
#   ./export-pdf.sh other-name.pdf
#
# Needs network access the first time for the Arimo web font; offline it falls
# back to Arial / Liberation Sans, which have the same metrics.
set -euo pipefail
cd "$(dirname "$0")"
OUT="${1:-misinformation-monitoring-poster-A1.pdf}"
CHROME="${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}"
if [ -z "${CHROME}" ]; then
  echo "ERROR: Google Chrome or Chromium is required (set CHROME=/path/to/chrome)" >&2
  exit 1
fi
"${CHROME}" --headless=new --disable-gpu --no-pdf-header-footer --allow-file-access-from-files \
  --virtual-time-budget=10000 --print-to-pdf="${PWD}/${OUT}" "file://${PWD}/Main.dc.html" 2>/dev/null
echo "Wrote ${PWD}/${OUT}"
command -v pdfinfo >/dev/null && pdfinfo "${OUT}" | grep -E "^(Pages|Page size)"
