#!/usr/bin/env bash
set -euo pipefail

ORGS=(3 5 10 15 20)
SAMPLES=(10 50 100 150 200)

DATA="data/translated_masked_samples_test.json"
OUTDIR="results/local"

mkdir -p "$OUTDIR"

wait_for_engine() {
  echo "  Waiting for flagging-engine..."
  until [ "$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    http://localhost:8004/predict -d '{}' 2>/dev/null)" = "422" ]; do
    sleep 5
  done
  echo "  Engine ready."
}

wait_for_gateway() {
  echo "  Waiting for gateway..."
  until [ "$(curl -s -o /dev/null -w '%{http_code}' \
    -H 'X-API-Key: stress-key' http://localhost:8000/api/status 2>/dev/null)" = "200" ]; do
    sleep 5
  done
  echo "  Gateway ready."
}

for N in "${ORGS[@]}"; do
  echo ""
  echo "========================================"
  echo "  ORGS=$N"
  echo "========================================"

  docker rm -f $(docker ps -aq) 2>/dev/null || true

  ./startup.sh --orgs "$N" --test-samples 10 --skip-caliper

  docker compose up -d --build

  wait_for_gateway
  wait_for_engine

  for S in "${SAMPLES[@]}"; do
    echo ""
    echo "--- orgs=$N samples=$S ---"
    python3 benchmarks/feed_samples.py \
      --data "$DATA" \
      --samples "$S" \
      --ai-pct 50 \
      --mode direct \
      --reject-pct 25 \
      --seed 100 \
      --endpoint http://localhost:8004 \
      --blockchain-api http://localhost:8000 \
      --num-orgs "$N" \
      --output "$OUTDIR/real_loads_org${N}_samples${S}.csv"
  done

  docker compose down
done

echo ""
echo "All benchmarks complete. Results in $OUTDIR/"
