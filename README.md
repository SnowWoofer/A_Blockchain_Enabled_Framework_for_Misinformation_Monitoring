# A Blockchain Enabled Framework For Misinformation Monitoring

Consortium blockchain framework for monitoring misinformation. An AI model
(AfroXLM-R, an `XLMRobertaForSequenceClassification` — ~2.2 GB, FP32) acts as a
data scout — flagging potential misinformation and escalating flagged reports to
a consortium of organizations who fact-check and vote on them via Hyperledger
Fabric. The chain anchors only a SHA-256 hash + off-chain URI per report; full
content lives in IPFS.

The system is benchmarkable **without the AI model running**: the benchmarks
measure blockchain throughput *per report*, and reports carry their AI verdict
(label + confidence) as precomputed data. Live inference is optional.

## How it works

The system has **two layers** that run independently:

### Blockchain layer (`./startup.sh`)

* **Fabric network**: peers/orderers/CouchDB on the `fabric_test` docker network
* **Chaincode** (Go, `blockchain/chaincode/misinformation/`): fact-check consensus model

  * `Submit` — an org submits a report (PENDING, 72h fact-check window)
  * `SubmitFactCheck` — another org records a verdict (`"0"`=non-misinfo, `"1"`=misinfo). After each vote the chaincode applies the consensus rule with
    `required = ceil(2/3 × registered_orgs)`:

    * **Early acceptance → FINAL**: once `tally["1"] >= required` (a 2/3 supermajority of YES).
    * **Early rejection → REJECTED**: once `tally["1"] + remaining_unvoted < required` — even if every remaining org later votes YES, the threshold is mathematically unreachable, so the report is finalised immediately without waiting for the rest.
  * `FinalizeReport` — closes an UNDER_REVIEW report (≥2 fact-checks, no tie)
  * `ExpireReport` — expires PENDING reports past their deadline
  * `RegisterOrg` / `RequestOrgAdmission` / `VoteOnOrgAdmission` / `FinalizeOrgAdmission` — org governance (admission needs 2/3 of registered orgs)
* **Fabric Gateway SDK sidecar** (`:9100`): Node.js wrapper around `@hyperledger/fabric-gateway`
* **IPFS Gateway** (`:9101`): thin add/cat bridge over kubo
* **Blockchain Gateway** (`:8000`): FastAPI, the only thing orgs talk to. Dual auth (API-key for benchmarks, JWT for demos), IPFS storage, chaincode invocation

### Application pipeline (`docker compose up -d --build`)

* **Kafka** (KRaft, single-node): topics `claims.raw`, `claims.flagged`
* **Claim Ingest Worker** (`:8003`): accepts raw claim texts, publishes to `claims.raw`
* **Flagging Engine** (`:8004`): AI model, Kafka consumer/producer + dynamic batching + Prometheus metrics
* **Submission Worker** (`:8001`): consumes flagged claims, submits to blockchain gateway
* **Fact-Checking Service** (`:8002`): lets orgs review/interact with pending claims
* **Prometheus** (`:9090`) + **Grafana** (`:3000`): monitoring

> The application pipeline is optional for benchmarking. The gateway layer
> (`:8000`, `:9100`, `:9101`) plus the Fabric network is all the benchmark
> harnesses require. The flagging-engine model is ~2.2 GB (AfroXLM-R-large
> FP32) and runs on small hosts — the whole stack fits in ~5-6 GB. Its only
> hard dependency is Kafka (`kafka:9092`). See [Benchmarking without the AI
> model](#benchmarking-without-the-ai-model).

The application pipeline routes claims as follows: `POST /ingest` into
claim-ingest-worker (`:8003`) publishes to the Kafka topic `claims.raw`; the
flagging-engine (`:8004`, `/predict`) consumes it and publishes
`claims.flagged`; submission-worker (`:8001`) consumes that and submits each
report to the blockchain gateway (`:8000`, IPFS + chaincode); fact-checking-
service (`:8002`) lets orgs vote until the report is FINAL or REJECTED. The
benchmark harnesses (`feed_samples.py`) bypass this pipeline entirely and
submit reports directly to the gateway — that is the quantity being measured.

### AI as data scout

The AI model processes incoming claims and flags potential misinformation.
Flagged reports are escalated to consortium organizations who independently
fact-check them. Two submission modes:

* **Direct mode (default)** — org1 is the **AI stakeholder org**. It submits
  `--ai-pct%` of reports (the ones the AI processed); the remaining reports are
  submitted by a random `org2..orgN`. Every org except the report's submitter
  votes, so org1 votes on reports it did not submit. With `--ai-pct 100` org1
  submits everything.
* **Indirect mode** — a random org is chosen to submit every report — simulates
  a human-in-the-loop workflow where every org is a normal stakeholder.

## Architecture

![Architecture Diagram](./architecture.svg)

## Repository layout

|Path|Purpose|
|-|-|
|`startup.sh`|blockchain layer: deploy + load + gateways|
|`docker-compose.yml`|application pipeline: Kafka + services + monitoring|
|`benchmarks/`|`feed_samples.py` (pipeline + consensus sim), `load-http.py` (synthetic load), `caliper/` (Caliper suite)|
|`blockchain/scripts/`|deploy, bootstrap, gateway launchers|
|`blockchain/chaincode/misinformation/`|Go chaincode (fact-check consensus model)|
|`blockchain/explorer/`|Hyperledger Explorer UI|
|`apps/blockchain_gateway/`|FastAPI gateway (`:8000`) — IPFS + chaincode|
|`apps/fabric_gateway/`|Node.js SDK sidecar (`:9100`)|
|`apps/ipfs_gateway/`|IPFS add/cat bridge (`:9101`)|
|`apps/claim-ingest-worker/`|Kafka producer — raw claims|
|`apps/flagging-engine/`|AI model + Kafka consumer/producer (`model/` weights)|
|`apps/submission-worker/`|Kafka consumer — submits to blockchain|
|`apps/fact-checking-service/`|org review/interaction proxy|
|`apps/kafka/`|Kafka broker (KRaft)|
|`monitoring/`|Prometheus + Grafana provisioning|
|`documentation/`|architecture diagrams, source formats|
|`data/`|test samples (git-ignored)|
|`results/local/`|benchmark CSVs (`http.csv`, `caliper.csv`, `real_loads.csv`)|
|`info/`|development notes|

> Git-ignored: Python virtualenv, `blockchain/fabric-samples/`, `offchain.db`,
> `apps/flagging-engine/model/model.safetensors`, `data/`.

## Prerequisites

### System packages

|Tool|Why|Check|
|-|-|-|
|**Docker + Compose v2**|Runs peers/orderers/CouchDB/IPFS/Kafka/services|`docker compose version`|
|**Python 3.10+**|Gateway, bootstrap, loader script|`python3 --version`|
|**Go 1.22+**|Chaincode vendoring during deploy (tested with 1.24.0)|`go version`|
|**`curl`**|Pre-flight / verification|`curl --version`|
|**`jq`**|Chaincode lifecycle tooling|`jq --version`|
|**`git`**|Clone|`git --version`|

Docker images are pulled automatically on first run.

> **WARNING — Compose version:** use the modern Compose **v2** plugin. Legacy
> `docker-compose` v1.29.2 breaks the test network.

### Minimum RAM

The stack is light: the Fabric network (~100 MiB per peer — ~1 GB at 13 orgs),
Kafka (~512 MB), gateways/IPFS/monitoring, and the flagging-engine's 2.2 GB FP32
model all run on a typical laptop. Plan for ~6 GB total with everything
resident.

|What|Notes|
|-|-|
|Blockchain layer + benchmarks only|~2-3 GB|
|Full stack incl. flagging-engine (AfroXLM-R-large 2.2 GB FP32)|~5-6 GB|
|Server profile (5090, fp16 CUDA)|Needs the GPU box — see [the flagging-engine compose](apps/flagging-engine/docker-compose.yaml)|

## Platform compatibility

|Platform|Hosting the network|As an HTTP client|
|-|-|-|
|**Linux**|✅ fully supported|✅|
|**Windows**|✅ via **WSL2**|✅|
|**macOS**|⚠️ via a Linux VM (UTM/Lima/Colima/Docker Desktop)|✅|

## Setup

### 1. Clone the repo

```
git clone <your-repo-url> A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring
cd A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring
```

### 2. Provision `blockchain/fabric-samples/` (one-time)

```
cd blockchain/fabric-samples
curl -sSL https://raw.githubusercontent.com/hyperledger/fabric/main/scripts/bootstrap.sh \
  | bash -s -- 2.5.9 1.5.9
mv fabric-samples/test-network .
rm -rf fabric-samples
# Result: blockchain/fabric-samples/{bin,config,test-network}
```

### 3. Create the Python virtualenv (one-time)

```
python3 -m venv venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring
venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring/bin/pip install \
  -r apps/blockchain_gateway/app/v1-0-0/api/requirements.txt
```

### 4. Mint API keys (idempotent)

```
blockchain/scripts/bootstrap-keys.sh org1 org2 org3 ... orgN
# Creates: key-org1 -> org1 ... key-orgN -> orgN, stress-key -> org1
```

`startup.sh` bootstraps keys for every org automatically; this is only needed
for a clean manual setup.

### Auth mode (optional)

The gateway supports two authentication modes controlled by the `AUTH_MODE` environment variable:

|Mode|Auth method|Use case|
|-|-|-|
|`bootstrap` (default)|API keys via `X-API-Key` header|Benchmarks, load testing|
|`jwt`|JWT tokens via `Authorization: Bearer` header|Demos, onboarding flows|

**Environment variables:**

|Variable|Default|Description|
|-|-|-|
|`AUTH_MODE`|`bootstrap`|`bootstrap` or `jwt`|
|`JWT_SECRET`|random per process|Secret key for signing JWT tokens|
|`JWT_EXPIRY_S`|`86400`|Token expiry in seconds (24 hours)|

To use JWT mode:

```bash
export AUTH_MODE=jwt
export JWT_SECRET=my-secret-key    # optional, auto-generated if not set

# Register a user
curl -X POST http://localhost:8000/api/auth/register \
  -d '{"org":"org1","password":"secret123"}'

# Login and get a token
TOKEN=$(curl -X POST http://localhost:8000/api/auth/login \
  -d '{"org":"org1","password":"secret123"}' | jq -r '.token')

# Use the token
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/status
```

Benchmark harnesses (`feed_samples.py`, `load-http.py`) use the bootstrap API-key system and are unaffected by `AUTH_MODE`.

## Run it end to end

The system has **two layers** that start independently. Start the blockchain
layer first — the app layer depends on it being reachable.

### Step 0: Purge stale containers

```
docker rm -f $(docker ps -aq) 2>/dev/null
```

> Leftover containers from earlier consortium sizes repeatedly break builds
> and IPFS checkpoints. Always purge first.

### Step 1: Start the blockchain layer

```
./startup.sh --orgs 5 --test-samples 10 --skip-caliper
```

This runs 7 steps automatically:

1. Ensures Docker networks exist (`ensure-networks.sh`)
2. Provisions the Fabric network (peers, orderer, channel, chaincode)
3. Starts IPFS + IPFS Gateway
4. Bootstraps API keys for org1..orgN (idempotent — safe to re-run)
5. Starts Fabric Gateway SDK sidecar + blockchain gateway
6. Runs a validation load (10 synthetic requests to confirm the gateway works)
7. Optionally runs a Caliper benchmark (skip with `--skip-caliper`)

After this, the blockchain gateway is live at `:8000`. Scale the consortium
with `--orgs N` (default 3, **max 25**) — gateway keys, peers, orderer, and the
connection profile all follow automatically. Benchmark harnesses **auto-detect
the on-chain org count** via `GET /api/orgs`, so they always match the live
consortium regardless of what `--orgs` you provisioned. `startup.sh` sets the
`ORGS_LIST` for the gateways itself, so the gateway org map is always in sync
on this path (only the incremental path below needs a manual gateway restart).

### Step 2: Start Explorer (patched)

```
docker compose -f blockchain/explorer/docker-compose.yaml down -v
docker compose -f blockchain/explorer/docker-compose.yaml up -d --build
sleep 30
```

Verify Explorer sync (should show `lscc` fallback, no errors):

```
docker logs explorer 2>&1 | grep -i "error\|lscc\|sync" | tail -10
```

### Step 3: Start the application pipeline (optional)

```
docker compose up -d --build
```

Brings up Kafka, claim-ingest-worker, flagging-engine, submission-worker,
fact-checking-service, Prometheus, and Grafana. The gateway services (`:9100`,
`:9101`, `:8000`) are **deliberately not part of this file** — they are
provisioned by `startup.sh` with the exact org count it deployed. Keeping them
out means an app-layer `docker compose up` can never recreate a gateway with a
stale/partial org map, which silently made every fact-check vote from orgs
beyond the default list fail (see [Troubleshooting](#troubleshooting)). The
flagging-engine's 2.2 GB model fits comfortably on a laptop.

> Kafka must be reachable at `kafka:9092` — `claim-ingest-worker`,
> `submission-worker` and the flagging-engine all fail hard at startup if it
> isn't (see [Troubleshooting](#troubleshooting)).
>
> **Engine warmup:** `flagging-engine` binds `:8004` *before* its model
> finishes loading (~1 min int8 quantize on CPU). Requests in that window get
> `Connection refused` / `Connection reset`; the service has `restart:
> unless-stopped`, so a Kafka hiccup on first boot self-heals. Don't feed until
> the `until …422` probe below prints `engine ready`.

### Step 4: Wait for all services

```
# Check gateway
curl -s -H "X-API-Key: stress-key" http://localhost:8000/api/status

# Check IPFS nodes
for p in 5001 5101 5201; do
  curl -s -X POST http://localhost:$p/api/v0/version >/dev/null \
    && echo "ipfs :$p OK" || echo "ipfs :$p DOWN"
done

# Wait for AI engine to be ready (returns 422)
until [ "$(curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:8004/predict -d '{}' 2>/dev/null)" = "422" ]; do
  sleep 5
done && echo "engine ready"
```

### Step 5: Feed data through the pipeline

```
python3 benchmarks/feed_samples.py \
  --data data/translated_masked_samples_test.json \
  --samples 10 \
  --ai-pct 70 \
  --mode direct \
  --reject-pct 20 \
  --seed 100
```

Results are appended to `results/local/real_loads.csv`.

|Flag|Purpose|Default|
|-|-|-|
|`--data`|JSON file with statements|`data/translated_masked_samples_test.json`|
|`--samples N`|Randomly select N samples|all|
|`--ai-pct P`|% of reports the AI processed (submitted by org1 in direct mode)|100|
|`--num-orgs N`|Override on-chain org count|**auto-detected** from `/api/orgs`|
|`--mode`|`direct` (org1 = AI stakeholder, submits ai_pct%, random orgs submit the rest, everyone-except-submitter votes) or `indirect` (random org submits each report)|direct|
|`--reject-pct P`|% of reports with sub-2/3 consensus (REJECTED)|0|
|`--fact-check`|Simulate fact-checking (consensus voting) after submission|**on**|
|`--no-fact-check`|Disable fact-checking|—|
|`--seed N`|Random seed for reproducibility|**100**|
|`--vote-pool N`|Max concurrent fact-check votes in flight per report (keep 1)|**1** (sequential — no Fabric MVCC conflicts)|
|`--endpoint`|Flagging-engine `/predict` base URL|`http://localhost:8004`|
|`--blockchain-api`|Blockchain gateway base URL|`http://localhost:8000`|
|`--output`|Output path (`.csv` for CSV, `.jsonl` for JSONL)|`results/local/real_loads.csv`|

**How it works:**

1. Loads JSON, randomly selects N samples
2. AI-verdict: if the model engine is reachable, `/predict` provides the label

   * confidence; otherwise the harness falls back to deterministic seeded draws
   (see [AI verdicts & confidence](#ai-verdicts--confidence))
3. Submission split — direct mode: `ai_pct%` submitted by org1, the rest by a
   random `org2..orgN`
4. Fact-checks one vote from each non-submitting org — subsequently polls
   `/api/reports/{id}/chain` until `FINAL` or `REJECTED`
   (all votes are cast first; the chain early-finalises the moment quorum is
   mathematically settled, so unneeded votes simply land on an already-closed
   report)
5. Outputs per-sample results to CSV + console summary

**Accepted vs Rejected:**

* **Accepted** (`FINAL`): fact-checkers reached `>= ceil(2/3 × registered_orgs)`
  YES votes — the report is finalised with label "1".
* **Rejected** (`REJECTED`): even if every remaining org voted YES the 2/3
  threshold is unreachable — e.g. 13 orgs (required 9): after 7 votes of which
  2 are YES, `tally[1] + remaining = 2 + 6 = 8 < 9`, so the maximum possible
  YES is 8 and the chain finalises rejection immediately; the remaining orgs
  never need to vote.

Both are on-chain with a `report_id` = **the IPFS CID** of the report blob.
Rejected reports are permanent evidence that consensus was attempted but failed.

### Step 6: Verify everything via API

```
# Check status (orgs, reports, blocks)
curl -s -H "X-API-Key: stress-key" http://localhost:8000/api/status

# Check report chain history
CID=QmfQxxokgmiia8A461KwMPCxzZxTpnxBoWSyrhNPzBq5hj
curl -s -H "X-API-Key: key-org1" "http://localhost:8000/api/reports/$CID/chain"

# Check full consensus lifecycle
curl -s -H "X-API-Key: key-org1" "http://localhost:8000/api/reports/$CID/history"

# Check IPFS content retrieval
curl -sL "http://localhost:8081/ipfs/$CID"

# Verify IPFS integrity
curl -s -H "X-API-Key: key-org1" "http://localhost:8000/api/reports/$CID/verify"
```

### Step 7: Check Explorer UI

```
Open http://localhost:8080
Login: exploreradmin / exploreradminpw
Navigate to: Network > Channels > mychannel > Blocks
```

### Growing the consortium on a live network (no reset)

`startup.sh --orgs N` always **rebuilds from scratch** — its `deploy.sh` tears
the network down first, so every `startup.sh` run starts a fresh ledger. To add
orgs to a **running** network without losing state, extend incrementally
(chain-level: peers, channel config update, chaincode re-commit with the new
`OutOf(2, Org1..OrgN)` policy):

```
export N=15                                # example: grow to 15 orgs

blockchain/scripts/add-orgs.sh --orgs "${N}"          # peers + channel config + chaincode re-commit
blockchain/scripts/register-orgs.sh --limit "${N}"    # on-chain org registry 1..N → drives /api/orgs + quorum
blockchain/scripts/bootstrap-keys.sh org1 .. org"${N}" # API keys for every org (idempotent)

# CRITICAL — recreate BOTH gateways with the new org list. Each gateway builds
# its org→MSP map + identity wallet from ORGS at container start, and both
# gateway compose files declare `ORGS=${ORGS_LIST}` with **no fallback**: an
# unset variable fails loudly at compose config time instead of silently
# deploying a partial consortium. (An older gateway with the default
# `org1..org5` would instead 400 every submit/vote from a higher org and send
# `fc` far below the 2/3 quorum — see Troubleshooting.)
export ORGS_LIST=org1,org2,...,org"${N}"
blockchain/scripts/start-gateway-service.sh up        # fabric-gateway SDK sidecar (:9100)
blockchain/scripts/start-blockchain-gateway.sh        # blockchain-gateway (:8000)

# refresh derived configs for the new org count
bash blockchain/scripts/gen-explorer-config.sh --orgs "${N}"
bash benchmarks/caliper/gen-caliper-config.sh --orgs "${N}" --samples 100
```

Quorum is recomputed automatically by the chaincode: `required = ceil(2/3 × N)`
(15 orgs → 10 YES votes), and `feed_samples.py` re-detects it via `/api/orgs` —
no manual org count anywhere. Note `add-orgs.sh` requires the org3 baseline
(org3 crypto) to exist, i.e. a prior `deploy.sh` run.

### Step 3: Feed data through the pipeline

```
python3 benchmarks/feed_samples.py \
  --data data/translated_masked_samples_test.json \
  --samples 200 \
  --ai-pct 70 \
  --mode direct \
  --reject-pct 20 \
  --seed 100
```

Results are appended to `results/local/real_loads.csv`.

|Flag|Purpose|Default|
|-|-|-|
|`--data`|JSON file with statements|`data/translated_masked_samples_test.json`|
|`--samples N`|Randomly select N samples|all|
|`--ai-pct P`|% of reports the AI processed (submitted by org1 in direct mode)|100|
|`--num-orgs N`|Override on-chain org count|**auto-detected** from `/api/orgs`|
|`--mode`|`direct` (org1 = AI stakeholder, submits ai_pct%, random orgs submit the rest, everyone-except-submitter votes) or `indirect` (random org submits each report)|direct|
|`--reject-pct P`|% of reports with sub-2/3 consensus (REJECTED)|0|
|`--fact-check`|Simulate fact-checking (consensus voting) after submission|**on**|
|`--no-fact-check`|Disable fact-checking|—|
|`--seed N`|Random seed for reproducibility|**100**|
|`--vote-pool N`|Max concurrent fact-check votes in flight per report (keep 1)|**1** (sequential — no Fabric MVCC conflicts)|
|`--endpoint`|Flagging-engine `/predict` base URL|`http://localhost:8004`|
|`--blockchain-api`|Blockchain gateway base URL|`http://localhost:8000`|
|`--output`|Output path (`.csv` for CSV, `.jsonl` for JSONL)|`results/local/real_loads.csv`|

**How it works:**

1. Loads JSON, randomly selects N samples
2. AI-verdict: if the model engine is reachable, `/predict` provides the label

   * confidence; otherwise the harness falls back to deterministic seeded draws
   (see [AI verdicts & confidence](#ai-verdicts--confidence))
3. Submission split — direct mode: `ai_pct%` submitted by org1, the rest by a
   random `org2..orgN`
4. Fact-checks one vote from each non-submitting org — subsequently polls
   `/api/reports/{id}/chain` until `FINAL` or `REJECTED`
   (all votes are cast first; the chain early-finalises the moment quorum is
   mathematically settled, so unneeded votes simply land on an already-closed
   report)
5. Outputs per-sample results to CSV + console summary

**Accepted vs Rejected:**

* **Accepted** (`FINAL`): fact-checkers reached `>= ceil(2/3 × registered_orgs)`
  YES votes — the report is finalised with label "1".
* **Rejected** (`REJECTED`): even if every remaining org voted YES the 2/3
  threshold is unreachable — e.g. 13 orgs (required 9): after 7 votes of which
  2 are YES, `tally[1] + remaining = 2 + 6 = 8 < 9`, so the maximum possible
  YES is 8 and the chain finalises rejection immediately; the remaining orgs
  never need to vote.

Both are on-chain with a `report_id` = **the IPFS CID** of the report blob.
Rejected reports are permanent evidence that consensus was attempted but failed.

### AI verdicts & confidence

* **With the engine up**: label/confidence come straight from
  `POST /predict` (`flagged`, `confidence`/`misinformation_probability`).
* **Without the engine** (it's down or you don't run it): the harness falls
  back to **deterministic seeded draws** — `label = rng.random() < 0.5`,
  `confidence = rng.uniform(0.5, 1.0)` from `--seed` (default 100). The
`model_label` CSV column records `fallback_random` for AI-pool rows and
`random` for non-AI-pool rows. These values are reproducible but **not**
model output or ground truth — the consensus/throughput results are
unaffected either way.

### Sanity check

```
curl -s -H "X-API-Key: stress-key" http://localhost:8000/api/status
# -> {"backend":"ipfs","ipfs_available":true,...}
```

## Benchmarking

All benchmarks save results as CSV files to `results/local/` automatically
(no extra flags needed). Timestamps are embedded as a column; runs **append**
rather than overwrite.

* `results/local/http.csv` — `load-http.py` results
* `results/local/caliper.csv` — Caliper benchmark results (one row per round)
* `results/local/real_loads.csv` — `feed_samples.py` results (one row per
  submitted report; columns include `org_count`, `samples`, `votes_cast`,
  `final_status`, `latency_ms`, model label/confidence)

### Load benchmark matrix

The throughput dataset is a **matrix of org counts × sample sizes**: one
`feed_samples.py` step per `<orgs, samples>` cell, with `samples ∈ {10, 50, 100, 150}` (each 310 rows) and `--seed 100` so every cell draws the
same sample pool for apples-to-apples latency scaling. The compiled final
dataset (see `final_data.csv`) spans org counts **{20, 15, 10, 3}** — **1,240
rows, 0 FAIL**, with a consistent 25 % `REJECTED` (from `--reject-pct 25`).
After purging and rebuilding for each org count (`docker rm -f` →
`startup.sh --orgs N` → `docker compose up -d --build`), feed all four step
sizes:

```
for S in 10 50 100 150; do
  python3 benchmarks/feed_samples.py \
    --data data/translated_masked_samples_test.json \
    --samples "$S" --ai-pct 50 --mode direct --reject-pct 25 --seed 100 \
    --endpoint http://localhost:8004 --blockchain-api http://localhost:8000 \
    --num-orgs N --output results/local/real_loads.csv
done
```

Each step appends to `real_loads.csv` **only on step completion** — a killed
step banks nothing and is replayed verbatim (same seed → same samples). With
the sequential `--vote-pool 1` default, a typical cell yields `fc = N-1` votes
(quorum `ceil(2N/3)`, ~25% REJECTED from `--reject-pct 25`) and **0 FAILs**.

**Compiling the final dataset** — `final_data.csv` at the repo root carries
every cell with normalised `samples` (the step size, recovered from each
write-batch row count) and `org_count`, aligned
`org_count → samples → timestamp`:

```
python3 - <<'EOF'
import csv
by = {}
for r in csv.DictReader(open('results/local/real_loads.csv')):
    r['org_count'] = r['org_count'].strip() or '10'      # older runs left it blank
    by.setdefault((r['org_count'], r['timestamp']), 0)
    by[(r['org_count'], r['timestamp'])] += 1
rows = [dict(r, samples=str(by[(r['org_count'], r['timestamp'])]))
        for r in csv.DictReader(open('results/local/real_loads.csv'))]
rows.sort(key=lambda r: (int(r['org_count']), int(r['samples']), r['timestamp']))
with open('final_data.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)
EOF
```

### `feed_samples.py` (realistic pipeline + consensus)

Feeds real sample data through the pipeline with predetermined
acceptance/rejection rates — exercises the full submit + vote consensus path:

```
python3 benchmarks/feed_samples.py \
  --samples 500 --ai-pct 80 --mode direct --reject-pct 15 --seed 100
# -> results/local/real_loads.csv
```

### `load-http.py` (synthetic stress test)

Bypasses the AI pipeline entirely — sends synthetic reports directly to the
blockchain gateway:

```
python3 benchmarks/load-http.py --samples 200 --rw-mix 100
# -> results/local/http.csv
```

|Flag|Purpose|Default|
|-|-|-|
|`--base`|Gateway base URL|`http://localhost:8000`|
|`--samples N`|Total requests (fixed mode)|200|
|`--rw-mix P`|% writes vs reads|100 (all writes)|
|`--concurrency C`|Parallel workers|24|
|`--rate R`|Per-worker pacing (fixed mode)|25|
|`--rps R`|Stream mode: global target rate|off|
|`--duration S`|Stream mode: run for S seconds|off|
|`--label`|report_id prefix|`stress`|

### Caliper benchmark

```
# 1. (re)generate the connection profile + round sizes to match the LIVE consortium
benchmarks/caliper/gen-caliper-config.sh --orgs <N> --samples 100
#    (orgs MUST match the on-chain count; writes = samples*10 @25 TPS, reads = samples*10 @50 TPS)

# 2. run it
benchmarks/caliper/run-caliper.sh
```

Results: `benchmarks/caliper/report.html` + `results/local/caliper.csv`
(one row per round — write *and* read are both captured).

### Hyperledger Explorer

```
docker compose -f blockchain/explorer/docker-compose.yaml down -v
docker compose -f blockchain/explorer/docker-compose.yaml up -d --build
# open http://localhost:8080 — login exploreradmin / exploreradminpw
# (connection profile is regenerated by deploy.sh / gen-explorer-config.sh for the current org count)
```

> **Note:** The Explorer image is patched to handle Fabric 2.5.x (missing `lscc`
> system chaincode). The `Dockerfile` wraps the `lscc` call in try-catch so it
> falls back to `_lifecycle`. Use `--build` on first run or after any Explorer
> image update.

Stop with the same compose file `down` (use `down -v` only to wipe Explorer's
own postgres/wallet data).

### Benchmarking without the AI model

The benchmarks measure **blockchain throughput per report** and never call the
model for that purpose — reports simply carry their AI verdict as data. The
flagging-engine is small (2.2 GB) and can run on the same host; you only need
to *skip* it if you'd rather not pay the model-load time or want a truly
minimal footprint:

1. `./startup.sh --orgs N ...` — chain + gateways + IPFS
2. `docker compose up -d` — Kafka + workers + flagging-engine all resident
   (~5-6 GB total)
3. Run any/all of the three benchmarks above

On a GPU box (e.g. 5090), run the fp16 CUDA server profile for fast inference:

```
docker compose -f apps/flagging-engine/docker-compose.yaml build \
  --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124
TORCH_DEVICE=cuda MODEL_QUANTIZATION=fp16 MAX_BATCH_SIZE=64 MAX_QUEUE_DELAY_MS=5 \
  docker compose -f apps/flagging-engine/docker-compose.yaml up -d
```

## Verifying a report end-to-end

```
CID=<report_id_from_csv>                       # CSV column report_id == IPFS CID

# on-chain record (status, consensus, votes)   — the ledger
curl -s -H "X-API-Key: key-org1" "http://localhost:8000/api/reports/$CID/chain"

# the report blob itself                       — the IPFS gateway (kubo :8081)
curl -sL "http://localhost:8081/ipfs/$CID"

# tamper-evidence check                        — hash + URI consistency
curl -s -H "X-API-Key: key-org1" "http://localhost:8000/api/reports/$CID/verify"

# full transaction history                     — Fabric GetHistoryForKey
curl -s -H "X-API-Key: key-org1" "http://localhost:8000/api/reports/$CID/history"
```

### Interpreting the `/chain` response

Returns the current on-chain record for a report. Example:

```json
{
  "id": "QmcA77cXb4koDoaeAw256dC7dSH3rrUMtM2rnsidispdJy",
  "content_hash": "73fa64843dd22e8b413d4c7f5c35946ba661632fbd887eec542cabf636072672",
  "inference_label": "0",
  "confidence": 0.573802,
  "model_version": "afro-xlmr-large-76L-misinfo-v1",
  "timestamp": "2026-09-18T22:47:32.509320Z",
  "submitted_by": "Org2MSP",
  "fact_check_deadline": "2026-09-21T22:47:32Z",
  "status": "FINAL",
  "fact_checks": [
    {"checker_msp": "Org3MSP", "outcome": "1", "txid": "09a21022..."},
    {"checker_msp": "Org1MSP", "outcome": "1", "txid": "e8e338c0..."}
  ],
  "final_label": "1",
  "finalized_by": "Org1MSP",
  "finalized_at": "2026-09-18T22:47:36Z"
}
```

| Field | What it tells you |
|-------|-------------------|
| `id` | IPFS CID — the report's globally unique identifier |
| `content_hash` | SHA-256 of `{source, inference}` — used for tamper detection |
| `inference_label` | ML model's initial guess: `"0"` = non-misinfo, `"1"` = misinfo |
| `confidence` | Model certainty (0.0–1.0) — values below 0.6 indicate uncertainty |
| `model_version` | Which ML model produced the inference |
| `submitted_by` | MSP ID of the org that submitted the report |
| `fact_check_deadline` | 72h voting window — after this the report can be expired |
| `status` | Lifecycle state (see below) |
| `fact_checks` | Array of org verdicts — each entry is one org's vote |
| `final_label` | Consensus result: `"0"` = verified non-misinfo, `"1"` = verified misinfo |
| `finalized_by` | Org that triggered finalization |
| `finalized_at` | When finalization occurred |

**Status values:** `PENDING` (no votes yet) → `UNDER_REVIEW` (≥1 vote) → `FINAL` (consensus reached) or `REJECTED` (mathematically impossible to reach consensus) or `EXPIRED` (deadline passed).

**What to look for:**
- `status: FINAL` with `final_label: "1"` = content verified as **misinformation**
- `status: FINAL` with `final_label: "0"` = content verified as **non-misinformation**
- `status: REJECTED` = fact-checkers couldn't reach 2/3 consensus — report is permanently marked as inconclusive
- `fact_checks` length < 2 with `status: UNDER_REVIEW` = still waiting for more votes
- `confidence` near 0.5 = the ML model was uncertain (borderline case)
- `content_hash` should match the IPFS document's `content_hash` (check with `/verify`)

### Interpreting the `/ipfs/{CID}` response

Returns the raw report document stored in IPFS. This is the off-chain blob containing the full claim text and AI analysis. Example:

```json
{
  "msg_id": "feed_25243384512c",
  "source": {
    "platform": "ai_scout",
    "content": "I-akhawunti esemthethweni ye-IEC ithe ward yethu ibhekene nokucishwa KAMANZI ngesikhathi sovoto...",
    "published_at": ""
  },
  "inference": {
    "label": "0",
    "confidence": 0.956888,
    "model_version": "afro-xlmr-large-76L-misinfo-v1",
    "inference_timestamp": "2026-09-18T22:47:38.840403Z"
  },
  "submitter": {
    "org_mspid": "Org2MSP",
    "submitted_at": "2026-09-18T22:47:38.840424Z"
  },
  "fact_check_status": "Pending",
  "fact_checks": [],
  "content_hash": "b7887c5d5849729bf4e4a8b60f1a8bbdd648fda326045240cfeb531202acc5f9"
}
```

| Field | What it tells you |
|-------|-------------------|
| `msg_id` | Claim identity carried from the ingest pipeline (NOT the report's own ID) |
| `source.content` | The original claim text being evaluated |
| `source.platform` | Where the claim originated (`ai_scout`, `twitter`, `facebook`, etc.) |
| `source.published_at` | When the claim was originally published (may be empty) |
| `inference.label` | ML model's classification: `"0"` = non-misinfo, `"1"` = misinfo |
| `inference.confidence` | Model confidence score (0.0–1.0) |
| `inference.model_version` | Which ML model produced the inference |
| `inference.inference_timestamp` | When the inference was made |
| `submitter.org_mspid` | MSP ID of the org that submitted the report |
| `submitter.submitted_at` | When the report was submitted |
| `fact_check_status` | Lifecycle: `Pending` → `Under_Review` → `Verified_Misinformation` / `Verified_Non_Misinformation` |
| `fact_checks` | Array of fact-check entries (empty until orgs vote) |
| `content_hash` | SHA-256 of `{source, inference}` — should match the on-chain value |

When populated, each `fact_checks` entry contains:

| Field | What it tells you |
|-------|-------------------|
| `checker_msp` | MSP ID of the fact-checking org |
| `outcome` | Verdict: `"0"` = non-misinfo, `"1"` = misinfo |
| `reasoning` | Free-text explanation (off-chain only, not on the ledger) |
| `support` | List of supporting URLs/references |
| `timestamp` | When the fact-check was submitted |

**Note:** The IPFS document evolves over time — each fact-check and finalization creates a new immutable IPFS object (new CID). Old versions remain permanently resolvable. The `report_id` field is deliberately excluded from the IPFS blob (it is the CID itself).

**What to look for:**
- `fact_checks[].reasoning` gives the human-readable justification (not available on-chain)
- `fact_checks[].support` provides evidence URLs for the verdict
- `content_hash` must match the on-chain `content_hash` — if not, the off-chain copy was tampered with
- `fact_check_status` should align with the on-chain `status` and `final_label`

### Interpreting the `/verify` response

Returns a tamper-evidence check comparing the off-chain IPFS document against the on-chain hash. Example:

```json
{
  "report_id": "QmcA77cXb4koDoaeAw256dC7dSH3rrUMtM2rnsidispdJy",
  "off_chain_intact": true,
  "matches_on_chain": true,
  "verified": true,
  "explanation": "The off-chain copy is unmodified AND its hash matches the immutable, consortium-voted hash stored on the ledger."
}
```

| Field | What it tells you |
|-------|-------------------|
| `off_chain_intact` | `true` = IPFS document hasn't been modified (re-hashed content matches stored `content_hash`) |
| `matches_on_chain` | `true` = IPFS `content_hash` equals the blockchain-anchored `content_hash` |
| `verified` | `true` = both checks pass — document is authentic and unmodified |
| `explanation` | Human-readable summary of the verification result |

**What to look for:**
- `verified: true` = the report is trustworthy — the off-chain document matches what was anchored on the blockchain
- `off_chain_intact: false` = the IPFS document was modified after submission (tampered)
- `matches_on_chain: false` = the off-chain copy was replaced with a different document entirely
- Both `false` = complete mismatch — the off-chain document is not the one that was submitted

### Interpreting the `/history` response

Returns the full transaction history for a report — every time it was modified on-chain. Example:

```json
[
  {
    "tx_id": "81a69f38e7944b91fe64922ac6625ca45fb8b60eb85fc8b635bccc926d216ff3",
    "timestamp": "2026-09-18T22:47:32Z",
    "is_delete": false,
    "record": {
      "id": "QmcA77cXb4koDoaeAw256dC7dSH3rrUMtM2rnsidispdJy",
      "status": "PENDING",
      "inference_label": "0",
      "confidence": 0.573802,
      "submitted_by": "Org2MSP",
      "fact_checks": []
    }
  },
  {
    "tx_id": "09a210221c66d7ef67375784e21d21b923ae1f1f97ba3eb1a518d9a6329a39f8",
    "timestamp": "2026-09-18T22:47:34Z",
    "is_delete": false,
    "record": {
      "id": "QmcA77cXb4koDoaeAw256dC7dSH3rrUMtM2rnsidispdJy",
      "status": "UNDER_REVIEW",
      "fact_checks": [
        {"checker_msp": "Org3MSP", "outcome": "1", "txid": "09a21022..."}
      ]
    }
  },
  {
    "tx_id": "e8e338c062c6f5c0d2fc22d3617c8f33efbe54df9337035d0aa20d16ad17eb25",
    "timestamp": "2026-09-18T22:47:36Z",
    "is_delete": false,
    "record": {
      "id": "QmcA77cXb4koDoaeAw256dC7dSH3rrUMtM2rnsidispdJy",
      "status": "FINAL",
      "fact_checks": [
        {"checker_msp": "Org3MSP", "outcome": "1", "txid": "09a21022..."},
        {"checker_msp": "Org1MSP", "outcome": "1", "txid": "e8e338c0..."}
      ],
      "final_label": "1",
      "finalized_by": "Org1MSP",
      "finalized_at": "2026-09-18T22:47:36Z"
    }
  }
]
```

| Field | What it tells you |
|-------|-------------------|
| `tx_id` | Unique Fabric transaction ID for this ledger write |
| `timestamp` | Blockchain timestamp (from the ordering service) |
| `is_delete` | `false` for all normal entries (reports are never deleted) |
| `record` | Full report state snapshot after this transaction |

**Lifecycle walkthrough (example above):**

| Entry | Time | What happened | Status | Fact-checks |
|-------|------|---------------|--------|-------------|
| 1st | `22:47:32` | `Org2MSP` submits content. ML model predicts non-misinfo (57% confidence). | `PENDING` | 0 |
| 2nd | `22:47:34` | `Org3MSP` submits verdict: **1** (misinformation). | `UNDER_REVIEW` | 1 (Org3MSP) |
| 3rd | `22:47:36` | `Org1MSP` submits verdict: **1** (misinformation). 2/2 votes = consensus. `Org1MSP` finalizes. | `FINAL` | 2 (Org3MSP, Org1MSP) |

**What to look for:**
- Entries are in **chronological order** — first entry is the original submission
- The **last entry's** `record.status` is the current on-chain status
- `record.fact_checks` grows with each entry — each new vote is appended
- If `record.status` is `UNDER_REVIEW` and `fact_checks` length < required votes, more fact-checking is pending
- The gap between timestamps shows how fast consensus was reached (seconds vs hours)
- `final_label` only appears in the entry where finalization occurred
- If `is_delete: true` appears, the key was deleted from the ledger (does not happen in normal operation)

## API quick reference

All endpoints require authentication. In bootstrap mode (default), use `-H "X-API-Key: <key>"`. In JWT mode, use `-H "Authorization: Bearer <token>"` after logging in via `/api/auth/login`.

### Blockchain gateway (`:8000`)

|Method & path|Purpose|
|-|-|
|`GET /api/status`|IPFS backend status|
|`POST /api/reports`|Submit a report (body: `msg_id`, `label` 0/1, `confidence`, `model_version`, `content`, `source_platform`)|
|`GET /api/reports`|List reports (optional `?status=PENDING`)|
|`GET /api/reports/{id}`|Report + off-chain payload|
|`GET /api/reports/{id}/chain`|On-chain record (hash, uri, status, fact-checks)|
|`GET /api/reports/{id}/verify`|Tamper-evidence check|
|`GET /api/reports/{id}/history`|Full tx/history via Fabric's `GetHistoryForKey`|
|`POST /api/reports/{id}/fact-check`|Org submits a fact-check verdict (body: `outcome` "0"/"1", `reasoning`, `support`)|
|`POST /api/reports/{id}/finalize`|Finalize a report (consensus reached)|
|`POST /api/reports/{id}/expire`|Expire a report past its voting deadline|
|`POST /api/orgs/apply`, `/api/orgs/{msp}/admission`, `/vote`, `/finalize`|New-org admission workflow|
|`GET /api/orgs`, `GET /api/orgs/{msp}/admission`|Registered orgs / admission status|
|`POST /api/auth/register`|Register user credentials (JWT mode only, body: `org`, `password`)|
|`POST /api/auth/login`|Login and get JWT token (JWT mode only, body: `org`, `password`)|

### Claim Ingest Worker (`:8003`)

|Method & path|Purpose|
|-|-|
|`POST /ingest`|Queue claims (body: `{"claims": ["text1", ...]}`)|
|`GET /health`|Health check|

### Flagging Engine (`:8004`)

|Method & path|Purpose|
|-|-|
|`POST /predict`|Direct inference (body: `{"post_text": "..."}`)|
|`GET /health`|Health check|

### Fact-Checking Service (`:8002`)

|Method & path|Purpose|
|-|-|
|`GET /claims/pending`|Pending claims this org hasn't fact-checked|
|`GET /claims/{id}`|Claim detail + prior fact-checks|
|`POST /claims/{id}/report`|Submit fact-check (body: `outcome` "0"/"1", `reasoning`, `support`)|
|`POST /claims/{id}/finalize`|Finalize claim|

## Teardown

```
# Application pipeline (Kafka, workers, monitoring)
docker compose down

# Blockchain layer
blockchain/scripts/deploy.sh down
blockchain/scripts/start-ipfs.sh down
blockchain/scripts/start-ipfs-gateway.sh down
blockchain/scripts/start-gateway-service.sh down
blockchain/scripts/start-blockchain-gateway.sh down

# Explorer (optional)
docker compose -f blockchain/explorer/docker-compose.yaml down   # or down -v to wipe its DB/wallet
```

## Troubleshooting

|Symptom|Cause / fix|
|-|-|
|`ERROR: unknown flag` from `startup.sh`|Only `--orgs`, `--test-samples`, `--skip-caliper` and `--help` exist.|
|`ERROR: API gateway unreachable (HTTP 000)`|Gateway not running. Run `./startup.sh` first.|
|Gateway returns `401` on `/api/status`|API keys never seeded — run `bootstrap-keys.sh`.|
|`ERROR: --orgs must be a positive integer (got 'org1,...,orgN')`|A shell exported the CSV org list into the `ORGS` integer variable. The startup scripts use a separate `ORGS_LIST`; don't clobber `ORGS`.|
|`docker compose: variable "ORGS_LIST" is not set` / Gateway returns `400 unknown signing org` / `fc` far below quorum|The gateways build their org→MSP map + identity wallet from `ORGS_LIST` at container start, and the gateway compose files now declare `ORGS=${ORGS_LIST}` with **no default** — an unset variable fails loudly at config time (on an older gateway with the `org1..org5` fallback it instead silently re-scoped the consortium and every submit/vote from a higher org 400'd, dropping `fc` below the 2/3 quorum). Fix: `export ORGS_LIST=org1,...,orgN` then recreate **both** gateways (`blockchain/scripts/start-gateway-service.sh up` + `blockchain/scripts/start-blockchain-gateway.sh`). The shared key DB already holds every org's key — no bootstrap needed. The `startup.sh` path sets `ORGS_LIST` from `--orgs N` automatically; the failure above only happens when you invoke app-layer compose or the gateway scripts without the env.|
|Kafka down / workers stuck `Restarting`|Kafka crashed (see below) or was never started. Restart: `docker compose -f apps/kafka/docker-compose.yml up -d` (data in the `kafka-data` volume survives — the entrypoint only formats storage once).|
|Kafka keeps dying with `Unable to send a heartbeat ... timed out` in the logs|A transient KRaft controller-heartbeat stall fenced the single-node broker (crash under host pressure). The real gap: the compose file has **no restart policy**, so the crash was permanent. Fix: restart (`docker compose -f apps/kafka/docker-compose.yml up -d`), and add `restart: unless-stopped` (already applied) so it self-heals in future.|
|`WARNING: /predict returned 0: [Errno 111] Connection refused` / `[Errno 104] Connection reset by peer`|flagging-engine not ready yet: uvicorn binds `:8004` *before* the model load completes (~1 min int8 quantize on CPU), and a first boot can die on a Kafka ref-crash then self-restart (`restart: unless-stopped`). Wait for `Application startup complete.` in `docker logs flagging-engine` or run the `until …422` probe before feeding — the 104 case when Kafka is genuinely down is covered below.|
|`model_label` shows `fallback_random` / `random`|The engine wasn't reachable, so label/confidence were seeded deterministically rather than model output. Not an error.|
|Caliper CSV only shows the write round|Fixed in `run-caliper.sh` (it now parses the summary table that contains the `query-all-reports-read` row). Re-run to get both rounds per timestamp.|
|Caliper `networkConfig.json` / `ccp.json` mismatch with the chain|Regenerate for the live consortium: `benchmarks/caliper/gen-caliper-config.sh --orgs <N>` and/or `blockchain/scripts/gen-explorer-config.sh --orgs <N>`.|
|`ModuleNotFoundError: No module named 'config'`|`.gitignore` `con*` rule hid `config.py`. Fixed in `81ca586`; re-pull the branch.|
|Flagging engine crashes on startup|Two common causes: (1) missing `model/config.json` or `model.safetensors` — ensure both exist in `apps/flagging-engine/model/`; (2) Kafka unreachable — the engine hard-fails its lifespan if `kafka:9092` is down ("Unable to bootstrap", `Application startup failed`). Start Kafka first.|
|Kafka connection refused from containers|Services must use `kafka:9092` (not `localhost:9094`). Check `config.py` defaults.|
|`docker-compose` legacy v1 errors|Use Compose v2 (`docker compose`). Remove stale `fabric_test` network.|
|`x509: certificate signed by unknown authority`|`blockchain/fabric-samples/` not provisioned correctly. Re-run Step 2.|
|Explorer shows stale topology|Regenerate for the current org count and recreate: `blockchain/scripts/gen-explorer-config.sh --orgs <N>` then `docker compose -f blockchain/explorer/docker-compose.yaml up -d`.|
|Report stuck in `UNDER_REVIEW`|Fact-checkers haven't reached consensus yet, or deadline hasn't passed. Check `GET /api/reports/{id}/chain`.|
|Report shows `REJECTED`|Fact-checkers couldn't reach >= 2/3 consensus. This is working as designed — see early rejection logic.|
|IPFS nodes crash-loop with `Error: lock /data/ipfs/repo.lock: permission denied`|The named IPFS volume carries ownership from an earlier container generation. `docker rm -f` does **not** remove volumes: run `docker compose down -v --remove-orphans` (also wipes app-layer state + IPFS volumes; legacy standalone `ipfs-data` may survive) then purge + rebuild from `startup.sh`.|
|Runs stop silently after a Windows reboot / lid-close / sleep|WSL terminates detached processes; `feed_samples.py` writes its CSV **only on step completion**, so a killed run banks nothing — replay the same seed (`--seed 100`) and identical samples are re-fed. No corruption, partial steps are simply lost.|
|Builds fail intermittently (keepalive ACK timeouts, high load, `ipfs … did not become ready`) with swap exhausted|Restore swap: `sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`. Note `sudo -n` (non-interactive) fails on this box — run such commands in an interactive shell.|
|`/api/auth/register` or `/api/auth/login` returns 400|`AUTH_MODE` is not set to `jwt`. Set `export AUTH_MODE=jwt` and restart the gateway.|
|JWT auth: all endpoints return 401 "missing Authorization header"|After login, pass the token as `-H "Authorization: Bearer <token>"`.|
|JWT auth: 401 "invalid or expired token"|Token expired (default 24h). Re-login via `/api/auth/login` to get a fresh token.|
|Apply org returns 400 "not registered on-chain"|The target org must be registered via `RegisterOrg` before `apply_org` can generate its API key. |
|IPFS nodes crash-loop with `Error: lock /data/ipfs/repo.lock: permission denied`|Run `docker run --rm -v ipfs_gateway_ipfs-data-1:/data -u root alpine chown -R 1000:1000 /data` (and `ipfs-data-2`), then `docker restart ipfs-node ipfs-node-1 ipfs-node-2`.|
|Explorer shows no blocks / sync errors|Use `--build` when starting Explorer: `docker compose -f blockchain/explorer/docker-compose.yaml up -d --build`. The patched image handles Fabric 2.5.x missing `lscc`.|