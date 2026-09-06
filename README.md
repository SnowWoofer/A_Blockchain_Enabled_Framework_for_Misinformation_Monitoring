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

- **Fabric network**: peers/orderers/CouchDB on the `fabric_test` docker network
- **Chaincode** (Go, `blockchain/chaincode/misinformation/`): fact-check consensus model
  - `SubmitReport` — an org submits a report (PENDING, 72h fact-check window)
  - `SubmitFactCheck` — another org records a verdict (`"0"`=non-misinfo, `"1"`=misinfo). After each vote the chaincode applies the consensus rule with
    `required = ceil(2/3 × registered_orgs)`:
    - **Early acceptance → FINAL**: once `tally["1"] >= required` (a 2/3 supermajority of YES).
    - **Early rejection → REJECTED**: once `tally["1"] + remaining_unvoted < required` — even if every remaining org later votes YES, the threshold is mathematically unreachable, so the report is finalised immediately without waiting for the rest.
  - `FinalizeReport` — closes an UNDER_REVIEW report (≥2 fact-checks, no tie)
  - `ExpireReport` — expires PENDING reports past their deadline
  - `RegisterOrg` / `RequestOrgAdmission` / `VoteOnOrgAdmission` / `FinalizeOrgAdmission` — org governance (admission needs 2/3 of registered orgs)
- **Fabric Gateway SDK sidecar** (`:9100`): Node.js wrapper around `@hyperledger/fabric-gateway`
- **IPFS Gateway** (`:9101`): thin add/cat bridge over kubo
- **Blockchain Gateway** (`:8000`): FastAPI, the only thing orgs talk to. API-key auth, IPFS storage, chaincode invocation

### Application pipeline (`docker compose up -d --build`)

- **Kafka** (KRaft, single-node): topics `claims.raw`, `claims.flagged`
- **Claim Ingest Worker** (`:8003`): accepts raw claim texts, publishes to `claims.raw`
- **Flagging Engine** (`:8004`): AI model, Kafka consumer/producer + dynamic batching + Prometheus metrics
- **Submission Worker** (`:8001`): consumes flagged claims, submits to blockchain gateway
- **Fact-Checking Service** (`:8002`): lets orgs review/interact with pending claims
- **Prometheus** (`:9090`) + **Grafana** (`:3000`): monitoring

> The application pipeline is optional for benchmarking. The gateway layer
> (`:8000`, `:9100`, `:9101`) plus the Fabric network is all the benchmark
> harnesses require. The flagging-engine model is ~2.2 GB (AfroXLM-R-large
> FP32) and runs on small hosts — the whole stack fits in ~5-6 GB. Its only
> hard dependency is Kafka (`kafka:9092`). See [Benchmarking without the AI
> model](#benchmarking-without-the-ai-model).

### Message flow

```
User/Script
   │
   ▼  POST /ingest {"claims":["text1",...]}
Claim Ingest Worker (:8003)
   │
   ▼  Kafka: topic=claims.raw
Flagging Engine (AI model)
   │
   ▼  Kafka: topic=claims.flagged (label + confidence)
Submission Worker (:8001)
   │
   ▼  POST /api/reports
Blockchain Gateway (:8000)
   │
   ▼  IPFS (off-chain) + Chaincode Submit (on-chain)
Fact-Checking Service → orgs fact-check → FINAL (2/3 YES) or REJECTED (impossible)
```

The benchmark harnesses (`feed_samples.py`) bypass this pipeline entirely and
submit reports directly to the gateway — that is the quantity being measured.

### AI as data scout

The AI model processes incoming claims and flags potential misinformation.
Flagged reports are escalated to consortium organizations who independently
fact-check them. Two submission modes:

- **Direct mode (default)** — org1 is the **AI stakeholder org**. It submits
  `--ai-pct%` of reports (the ones the AI processed); the remaining reports are
  submitted by a random `org2..orgN`. Every org except the report's submitter
  votes, so org1 votes on reports it did not submit. With `--ai-pct 100` org1
  submits everything.
- **Indirect mode** — a random org is chosen to submit every report — simulates
  a human-in-the-loop workflow where every org is a normal stakeholder.

## Architecture

![Architecture Diagram](./architecture.svg)

## Repository layout

```
.
├── startup.sh                         # blockchain layer: deploy + load + gateways
├── docker-compose.yml                 # application pipeline: Kafka + services + monitoring
├── benchmarks/
│   ├── feed_samples.py                # feed JSON samples through pipeline + consensus sim
│   ├── load-http.py                   # synthetic load driver -> gateway
│   └── caliper/                       # Hyperledger Caliper benchmark suite
├── blockchain/
│   ├── scripts/                       # deploy, bootstrap, gateway launchers, etc.
│   ├── chaincode/misinformation/      # Go chaincode (fact-check consensus model)
│   ├── fabric-samples/                # git-ignored runtime (provisioned once)
│   └── explorer/                      # Hyperledger Explorer UI
├── apps/
│   ├── ai_service/                    # AI service app (skeleton)
│   ├── blockchain_gateway/            # FastAPI gateway (:8000) — IPFS + chaincode
│   ├── fabric_gateway/                # Node.js SDK sidecar (:9100)
│   ├── ipfs_gateway/                  # IPFS add/cat bridge (:9101)
│   ├── claim-ingest-worker/           # Kafka producer — raw claims
│   ├── flagging-engine/               # AI model + Kafka consumer/producer
│   │   └── model/                     # model weights + tokenizer + config.json
│   ├── submission-worker/             # Kafka consumer — submits to blockchain
│   ├── fact-checking-service/         # org review/interaction proxy
│   └── kafka/                         # Kafka broker (KRaft)
├── monitoring/                        # Prometheus + Grafana provisioning
├── documentation/                     # architecture diagrams, source formats
├── data/                              # test samples (git-ignored)
│   └── translated_masked_samples_test.json
├── results/local/                     # benchmark CSVs (http.csv, caliper.csv, real_loads.csv)
└── info/                              # development notes
```

> Git-ignored: Python virtualenv, `blockchain/fabric-samples/`, `offchain.db`,
> `apps/flagging-engine/model/model.safetensors`, `data/`.

## Prerequisites

### System packages

| Tool | Why | Check |
|---|---|---|
| **Docker + Compose v2** | Runs peers/orderers/CouchDB/IPFS/Kafka/services | `docker compose version` |
| **Python 3.10+** | Gateway, bootstrap, loader script | `python3 --version` |
| **Go 1.22+** | Chaincode vendoring during deploy (tested with 1.24.0) | `go version` |
| **`curl`** | Pre-flight / verification | `curl --version` |
| **`jq`** | Chaincode lifecycle tooling | `jq --version` |
| **`git`** | Clone | `git --version` |

Docker images are pulled automatically on first run.

> **WARNING — Compose version:** use the modern Compose **v2** plugin. Legacy
> `docker-compose` v1.29.2 breaks the test network.

### Minimum RAM

The stack is light: the Fabric network (~100 MiB per peer — ~1 GB at 13 orgs),
Kafka (~512 MB), gateways/IPFS/monitoring, and the flagging-engine's 2.2 GB FP32
model all run on a typical laptop. Plan for ~6 GB total with everything
resident.

| What | Notes |
|---|---|
| Blockchain layer + benchmarks only | ~2-3 GB |
| Full stack incl. flagging-engine (AfroXLM-R-large 2.2 GB FP32) | ~5-6 GB |
| Server profile (5090, fp16 CUDA) | Needs the GPU box — see [the flagging-engine compose](apps/flagging-engine/docker-compose.yaml) |

## Platform compatibility

| Platform | Hosting the network | As an HTTP client |
|---|---|---|
| **Linux** | ✅ fully supported | ✅ |
| **Windows** | ✅ via **WSL2** | ✅ |
| **macOS** | ⚠️ via a Linux VM (UTM/Lima/Colima/Docker Desktop) | ✅ |

## Setup

### 1. Clone the repo

```bash
git clone <your-repo-url> A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring
cd A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring
```

### 2. Provision `blockchain/fabric-samples/` (one-time)

```bash
cd blockchain/fabric-samples
curl -sSL https://raw.githubusercontent.com/hyperledger/fabric/main/scripts/bootstrap.sh \
  | bash -s -- 2.5.9 1.5.9
mv fabric-samples/test-network .
rm -rf fabric-samples
# Result: blockchain/fabric-samples/{bin,config,test-network}
```

### 3. Create the Python virtualenv (one-time)

```bash
python3 -m venv venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring
venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring/bin/pip install \
  -r apps/blockchain_gateway/app/v1-0-0/api/requirements.txt
```

### 4. Mint API keys (idempotent)

```bash
blockchain/scripts/bootstrap-keys.sh org1 org2 org3 ... orgN
# Creates: key-org1 -> org1 ... key-orgN -> orgN, stress-key -> org1
```

`startup.sh` bootstraps keys for every org automatically; this is only needed
for a clean manual setup.

## Run it end to end

The system has **two layers** that start independently. Start the blockchain
layer first — the app layer depends on it being reachable.

### Step 1: Start the blockchain layer

```bash
./startup.sh --orgs 3 --test-samples 50 --skip-caliper
```

This runs 7 steps automatically:

1. Ensures Docker networks exist (`ensure-networks.sh`)
2. Provisions the Fabric network (peers, orderer, channel, chaincode)
3. Starts IPFS + IPFS Gateway
4. Bootstraps API keys for org1..orgN (idempotent — safe to re-run)
5. Starts Fabric Gateway SDK sidecar + blockchain gateway
6. Runs a validation load (50 synthetic requests to confirm the gateway works)
7. Optionally runs a Caliper benchmark (skip with `--skip-caliper`)

After this, the blockchain gateway is live at `:8000`. Scale the consortium
with `--orgs N` (default 3, **max 25**) — gateway keys, peers, orderer, and the
connection profile all follow automatically. Benchmark harnesses **auto-detect
the on-chain org count** via `GET /api/orgs`, so they always match the live
consortium regardless of what `--orgs` you provisioned. `startup.sh` sets the
`ORGS_LIST` for the gateways itself, so the gateway org map is always in sync
on this path (only the incremental path below needs a manual gateway restart).

> **Recommended workflow** — provision the network first with `startup.sh`,
> then run benchmark variants yourself against the resulting `N`-org
> consortium:
>
> ```bash
> ./startup.sh --orgs N --test-samples 10 --skip-caliper   # net + keys + gateways only
> docker compose up -d --build                              # AI pipeline (once; survives restarts)
> # ...then any of the benchmark variants under "Benchmarking"
> ```

### Step 2: Start the application pipeline (optional)

```bash
docker compose up -d --build
```

Brings up Kafka, claim-ingest-worker, flagging-engine, submission-worker,
fact-checking-service, Prometheus, and Grafana. The gateway services (`:9100`,
`:9101`, `:8000`) are also included here for convenience — running both
`startup.sh` and `docker compose up` is safe and idempotent (same containers,
no duplicates). The flagging-engine's 2.2 GB model fits comfortably on a laptop.

> Kafka must be reachable at `kafka:9092` — `claim-ingest-worker`,
> `submission-worker` and the flagging-engine all fail hard at startup if it
> isn't (see [Troubleshooting](#troubleshooting)).

### Growing the consortium on a live network (no reset)

`startup.sh --orgs N` always **rebuilds from scratch** — its `deploy.sh` tears
the network down first, so every `startup.sh` run starts a fresh ledger. To add
orgs to a **running** network without losing state, extend incrementally
(chain-level: peers, channel config update, chaincode re-commit with the new
`OutOf(2, Org1..OrgN)` policy):

```bash
export N=15                                # example: grow to 15 orgs

blockchain/scripts/add-orgs.sh --orgs "${N}"          # peers + channel config + chaincode re-commit
blockchain/scripts/register-orgs.sh --limit "${N}"    # on-chain org registry 1..N → drives /api/orgs + quorum
blockchain/scripts/bootstrap-keys.sh org1 .. org"${N}" # API keys for every org (idempotent)

# CRITICAL — recreate BOTH gateways with the new org list. Each gateway builds
# its org→MSP map from the ORGS env at container start (default fallback is
# org1..org5); extending via add-orgs alone leaves them stale and every
# submit/vote from an org > the old count fails with "400 unknown signing org".
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

```bash
python3 benchmarks/feed_samples.py \
  --data data/translated_masked_samples_test.json \
  --samples 200 \
  --ai-pct 70 \
  --mode direct \
  --reject-pct 20 \
  --seed 42
```

Results are appended to `results/local/real_loads.csv`.

| Flag | Purpose | Default |
|---|---|---|
| `--data` | JSON file with statements | `data/translated_masked_samples_test.json` |
| `--samples N` | Randomly select N samples | all |
| `--ai-pct P` | % of reports the AI processed (submitted by org1 in direct mode) | 100 |
| `--num-orgs N` | Override on-chain org count | **auto-detected** from `/api/orgs` |
| `--mode` | `direct` (org1 = AI stakeholder, submits ai_pct%, random orgs submit the rest, everyone-except-submitter votes) or `indirect` (random org submits each report) | direct |
| `--reject-pct P` | % of reports with sub-2/3 consensus (REJECTED) | 0 |
| `--fact-check` | Simulate fact-checking (consensus voting) after submission | **on** |
| `--no-fact-check` | Disable fact-checking | — |
| `--seed N` | Random seed for reproducibility | **100** |
| `--output` | Output path (`.csv` for CSV, `.jsonl` for JSONL) | `results/local/real_loads.csv` |

**How it works:**

1. Loads JSON, randomly selects N samples
2. AI-verdict: if the model engine is reachable, `/predict` provides the label
   + confidence; otherwise the harness falls back to deterministic seeded draws
   (see [AI verdicts & confidence](#ai-verdicts--confidence))
3. Submission split — direct mode: `ai_pct%` submitted by org1, the rest by a
   random `org2..orgN`
4. Fact-checks one vote at a time from the non-submitting orgs — stops as soon
   as the chain reports `FINAL` or `REJECTED`
5. Outputs per-sample results to CSV + console summary

**Accepted vs Rejected:**

- **Accepted** (`FINAL`): fact-checkers reached `>= ceil(2/3 × registered_orgs)`
  YES votes — the report is finalised with label "1".
- **Rejected** (`REJECTED`): even if every remaining org voted YES the 2/3
  threshold is unreachable — e.g. 13 orgs (required 9): after 7 votes of which
  2 are YES, `tally[1] + remaining = 2 + 6 = 8 < 9`, so the maximum possible
  YES is 8 and the chain finalises rejection immediately; the remaining orgs
  never need to vote.

Both are on-chain with a `report_id` = **the IPFS CID** of the report blob.
Rejected reports are permanent evidence that consensus was attempted but failed.

### AI verdicts & confidence

- **With the engine up**: label/confidence come straight from
  `POST /predict` (`flagged`, `confidence`/`misinformation_probability`).
- **Without the engine** (it's down or you don't run it): the harness falls
  back to **deterministic seeded draws** — `label = rng.random() < 0.5`,
  `confidence = rng.uniform(0.5, 1.0)` from `--seed` (default 100). The
  `model_label` CSV column records `fallback_random` for AI-pool rows and
  `random` for non-AI-pool rows. These values are reproducible but **not**
  model output or ground truth — the consensus/throughput results are
  unaffected either way.

### Sanity check

```bash
curl -s -H "X-API-Key: stress-key" http://localhost:8000/api/status
# -> {"backend":"ipfs","ipfs_available":true,...}
```

## Benchmarking

All benchmarks save results as CSV files to `results/local/` automatically
(no extra flags needed). Timestamps are embedded as a column; runs **append**
rather than overwrite.

```
results/local/
├── http.csv        # load-http.py results
├── caliper.csv     # Caliper benchmark results (one row per round)
└── real_loads.csv  # feed_samples.py results
```

### `feed_samples.py` (realistic pipeline + consensus)

Feeds real sample data through the pipeline with predetermined
acceptance/rejection rates — exercises the full submit + vote consensus path:

```bash
python3 benchmarks/feed_samples.py \
  --samples 500 --ai-pct 80 --mode direct --reject-pct 15 --seed 100
# -> results/local/real_loads.csv
```

### `load-http.py` (synthetic stress test)

Bypasses the AI pipeline entirely — sends synthetic reports directly to the
blockchain gateway:

```bash
python3 benchmarks/load-http.py --samples 200 --rw-mix 100
# -> results/local/http.csv
```

| Flag | Purpose | Default |
|---|---|---|
| `--base` | Gateway base URL | `http://localhost:8000` |
| `--samples N` | Total requests (fixed mode) | 200 |
| `--rw-mix P` | % writes vs reads | 100 (all writes) |
| `--concurrency C` | Parallel workers | 24 |
| `--rate R` | Per-worker pacing (fixed mode) | 25 |
| `--rps R` | Stream mode: global target rate | off |
| `--duration S` | Stream mode: run for S seconds | off |
| `--label` | report_id prefix | `stress` |

### Caliper benchmark

```bash
# 1. (re)generate the connection profile + round sizes to match the LIVE consortium
benchmarks/caliper/gen-caliper-config.sh --orgs <N> --samples 100
#    (orgs MUST match the on-chain count; writes = samples*10 @25 TPS, reads = samples*20 @50 TPS)

# 2. run it
benchmarks/caliper/run-caliper.sh
```

Results: `benchmarks/caliper/report.html` + `results/local/caliper.csv`
(one row per round — write *and* read are both captured).

### Hyperledger Explorer

```bash
docker compose -f blockchain/explorer/docker-compose.yaml up -d
# open http://localhost:8080 — login exploreradmin / exploreradminpw
# (connection profile is regenerated by deploy.sh / gen-explorer-config.sh for the current org count)
```

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

```bash
docker compose -f apps/flagging-engine/docker-compose.yaml build \
  --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124
TORCH_DEVICE=cuda MODEL_QUANTIZATION=fp16 MAX_BATCH_SIZE=64 MAX_QUEUE_DELAY_MS=5 \
  docker compose -f apps/flagging-engine/docker-compose.yaml up -d
```

## Verifying a report end-to-end

```bash
CID=<report_id_from_csv>                       # CSV column report_id == IPFS CID

# on-chain record (status, consensus, votes)   — the ledger
curl -s -H "X-API-Key: key-org1" "http://localhost:8000/api/reports/$CID/chain"

# the report blob itself                       — the IPFS gateway (kubo :8081)
curl -s "http://localhost:8081/ipfs/$CID"

# tamper-evidence check                        — hash + URI consistency
curl -s -H "X-API-Key: key-org1" "http://localhost:8000/api/reports/$CID/verify"

# full transaction history                     — Fabric GetHistoryForKey
curl -s -H "X-API-Key: key-org1" "http://localhost:8000/api/reports/$CID/history"
```

## API quick reference

All endpoints require `-H "X-API-Key: <key>"`.

### Blockchain gateway (`:8000`)

| Method & path | Purpose |
|---|---|
| `GET /api/status` | IPFS backend status |
| `POST /api/reports` | Submit a report (body: `msg_id`, `label` 0/1, `confidence`, `model_version`, `content`, `source_platform`) |
| `GET /api/reports` | List reports (optional `?status=PENDING`) |
| `GET /api/reports/{id}` | Report + off-chain payload |
| `GET /api/reports/{id}/chain` | On-chain record (hash, uri, status, fact-checks) |
| `GET /api/reports/{id}/verify` | Tamper-evidence check |
| `GET /api/reports/{id}/history` | Full tx/history via Fabric's `GetHistoryForKey` |
| `POST /api/reports/{id}/fact-check` | Org submits a fact-check verdict (body: `outcome` "0"/"1", `reasoning`, `support`) |
| `POST /api/reports/{id}/finalize` | Finalize a report (consensus reached) |
| `POST /api/reports/{id}/expire` | Expire a report past its voting deadline |
| `POST /api/orgs/apply`, `/api/orgs/{msp}/admission`, `/vote`, `/finalize` | New-org admission workflow |
| `GET /api/orgs`, `GET /api/orgs/{msp}/admission` | Registered orgs / admission status |

### Claim Ingest Worker (`:8003`)

| Method & path | Purpose |
|---|---|
| `POST /ingest` | Queue claims (body: `{"claims": ["text1", ...]}`) |
| `GET /health` | Health check |

### Flagging Engine (`:8004`)

| Method & path | Purpose |
|---|---|
| `POST /predict` | Direct inference (body: `{"post_text": "..."}`) |
| `GET /health` | Health check |

### Fact-Checking Service (`:8002`)

| Method & path | Purpose |
|---|---|
| `GET /claims/pending` | Pending claims this org hasn't fact-checked |
| `GET /claims/{id}` | Claim detail + prior fact-checks |
| `POST /claims/{id}/report` | Submit fact-check (body: `outcome` "0"/"1", `reasoning`, `support`) |
| `POST /claims/{id}/finalize` | Finalize claim |

## Teardown

```bash
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

| Symptom | Cause / fix |
|---|---|
| `ERROR: unknown flag` from `startup.sh` | Only `--orgs`, `--test-samples`, `--skip-caliper` and `--help` exist. |
| `ERROR: API gateway unreachable (HTTP 000)` | Gateway not running. Run `./startup.sh` first. |
| Gateway returns `401` on `/api/status` | API keys never seeded — run `bootstrap-keys.sh`. |
| `ERROR: --orgs must be a positive integer (got 'org1,...,orgN')` | A shell exported the CSV org list into the `ORGS` integer variable. The startup scripts use a separate `ORGS_LIST`; don't clobber `ORGS`. |
| Gateway returns `400 unknown signing org` on submit/fact-check | The gateway containers were created when `ORGS` held a smaller org list (`ORG_MSPID` is built from that env at container start, and the compose default falls back to `org1..org5`). Fix: `export ORGS_LIST=org1,...,orgN` then recreate **both** gateways (`blockchain/scripts/start-gateway-service.sh up` + `blockchain/scripts/start-blockchain-gateway.sh`). The shared key DB already holds every org's key — no bootstrap needed. This is the tell-tale symptom of extending the consortium and forgetting the gateway restart. |
| Kafka down / workers stuck `Restarting` | Kafka crashed (see below) or was never started. Restart: `docker compose -f apps/kafka/docker-compose.yml up -d` (data in the `kafka-data` volume survives — the entrypoint only formats storage once). |
| Kafka keeps dying with `Unable to send a heartbeat ... timed out` in the logs | A transient KRaft controller-heartbeat stall fenced the single-node broker (crash under host pressure). The real gap: the compose file has **no restart policy**, so the crash was permanent. Fix: restart (`docker compose -f apps/kafka/docker-compose.yml up -d`), and add `restart: unless-stopped` (already applied) so it self-heals in future. |
| `WARNING: /predict returned 0: [Errno 104] Connection reset by peer` | flagging-engine cannot start because Kafka is down, so it resets the connection mid-boot. Start Kafka (above) or run benchmarks without live inference (`feed_samples.py` falls back to seeded verdicts). |
| `model_label` shows `fallback_random` / `random` | The engine wasn't reachable, so label/confidence were seeded deterministically rather than model output. Not an error. |
| Caliper CSV only shows the write round | Fixed in `run-caliper.sh` (it now parses the summary table that contains the `query-all-reports-read` row). Re-run to get both rounds per timestamp. |
| Caliper `networkConfig.json` / `ccp.json` mismatch with the chain | Regenerate for the live consortium: `benchmarks/caliper/gen-caliper-config.sh --orgs <N>` and/or `blockchain/scripts/gen-explorer-config.sh --orgs <N>`. |
| `ModuleNotFoundError: No module named 'config'` | `.gitignore` `con*` rule hid `config.py`. Fixed in `81ca586`; re-pull the branch. |
| Flagging engine crashes on startup | Two common causes: (1) missing `model/config.json` or `model.safetensors` — ensure both exist in `apps/flagging-engine/model/`; (2) Kafka unreachable — the engine hard-fails its lifespan if `kafka:9092` is down ("Unable to bootstrap", `Application startup failed`). Start Kafka first. |
| Kafka connection refused from containers | Services must use `kafka:9092` (not `localhost:9094`). Check `config.py` defaults. |
| `docker-compose` legacy v1 errors | Use Compose v2 (`docker compose`). Remove stale `fabric_test` network. |
| `x509: certificate signed by unknown authority` | `blockchain/fabric-samples/` not provisioned correctly. Re-run Step 2. |
| Explorer shows stale topology | Regenerate for the current org count and recreate: `blockchain/scripts/gen-explorer-config.sh --orgs <N>` then `docker compose -f blockchain/explorer/docker-compose.yaml up -d`. |
| Report stuck in `UNDER_REVIEW` | Fact-checkers haven't reached consensus yet, or deadline hasn't passed. Check `GET /api/reports/{id}/chain`. |
| Report shows `REJECTED` | Fact-checkers couldn't reach >= 2/3 consensus. This is working as designed — see early rejection logic. |