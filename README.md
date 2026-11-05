# A Blockchain Enabled Framework For Misinformation Monitoring

Consortium blockchain framework for monitoring misinformation. An AI model
(AfroXLM-R, an `XLMRobertaForSequenceClassification`) acts as a
potential misinformation data scout allowing for flagging of potential misinformation and escalating flagged reports to
a consortium of organizations who fact checks and vote on them via Hyperledger
Fabric. The chain anchors only a SHA-256 hash + off-chain URI per report; full
content lives in IPFS.

The prototype adds three things on top of that pipeline:

* a **public transparency portal**, where anyone can check whether a claim has
  been fact-checked, read the verdict with each organisation's reasoning and
  evidence, verify the record has not been tampered with, and submit tips
  anonymously;
* a **fact-checker workspace**, with a review queue per organisation, blind
  review, verdicts with reasoning and evidence links, a tamper check and ledger
  history;
* an **officials' console**, for stalled reports, consortium admission votes,
  the organisation's user accounts and a tamper-evident audit log;

all served over HTTPS behind a hardened edge, with individual accounts, roles
mapped onto Fabric ABAC identities, and authenticated service-to-service
traffic.

## Quick start: one command up, one command down

```bash
python3 up.py        # bring the whole system up (secure prototype, 3 orgs)
python3 down.py      # shut everything down and clean up
```

`up.py` checks the prerequisites and removes anything left from a previous run.
It downloads the Fabric binaries if they are missing, generates secrets, and
starts the Fabric network, chaincode, IPFS and gateways (`startup.sh --secure`).
It then starts Kafka, the AI pipeline, the fact-checking service, the web client
and monitoring, and waits until each one is healthy. It then fills the review
queue: 10 claims picked from `data/test.json` with a fixed seed (so every
fresh deployment starts with the same claims) go through the real pipeline,
where the AI scores each one before it is anchored on the ledger. Only the
statement text is sent, not the dataset's label, so review stays blind. The
AI's org (org1) submits them, and every org reviews them, org1 included
(`checker.org1`, `checker.org2`, ...). At the end it prints where to sign in.
The first run downloads and builds everything, so it takes a while; later runs
reuse the images.

|Option|Effect|
|-|-|
|`--orgs N`|consortium size, 3 to 30 (default 3)|
|`--benchmark`|well-known API keys + validation load, for `feed_samples.py` / `load-http.py`|
|`--no-ai`|skip the flagging engine (2.2 GB model, ~3 GB RAM)|
|`--explorer`|also start Hyperledger Explorer|
|`--smoke`|finish by sending one tip through the whole pipeline to the ledger|
|`--seed-claims N`|claims from `data/test.json` to fill the review queue with (default 10; 0 for none, the default with `--benchmark`). Skipped with `--no-ai`. Re-run on its own with `python3 blockchain/scripts/seed-claims.py`|
|`--demo`|web client on the in-memory mock ledger, no Fabric (light)|
|`--dry-run`|run the checks and show the plan without changing anything|
|`--status`|only list the running containers with their IP addresses and how to reach them|

When the system is up, `up.py` prints a table of every running container. It
shows the container's IP address on each Docker network, the host address and
port to open it on (marked `[this host only]` or `[public]`), the LAN address
for public ports, and the name other containers use to reach it. Container IPs
change when a container is recreated, so use the host addresses to connect.

`down.py` stops and removes every container, volume, network, chaincode image
and runtime file the system created. It never touches `results/`, `data/`, the
model weights or the Fabric binaries. `python3 down.py --purge` also deletes
the generated secrets and the built images, and `--dry-run` shows what would be
removed. Container names are fixed, so both scripts also replace containers of
the same name started from another clone of this repository.

Output is mirrored to `logs/up-<timestamp>.log`.

> **Before the first run**, download the model weights (`model.safetensors`, 2.2 GB,
> not in git) from the [model folder on Google Drive](https://drive.google.com/drive/folders/1EcaSIEqh1-BTeSL_ThZse6P1IAhImncs?usp=sharing) and place them in
> `apps/flagging-engine/model/`, or the flagging engine is not started.

## Prerequisites

Python 3.10+, Docker with Compose v2, openssl, curl, jq, go and git, and about
6.5 GB of free memory for the default mode.

## Running the benchmarks

Start the system in benchmark mode, and shut it down afterwards with `python3 down.py`:

```bash
python3 up.py --benchmark
```

**Caliper:** use the same number of organisations as the deployment (add `--orgs N` to `up.py` for more than 3):

```bash
bash benchmarks/caliper/gen-caliper-config.sh --orgs 3 --samples 50
bash benchmarks/caliper/run-caliper.sh
```

`--samples 50` means 500 writes and 500 reads. The report is written to `benchmarks/caliper/report.html`. `gen-caliper-config.sh` rewrites `ccp.json` and `test-network.yaml` in `benchmarks/caliper/networks/fabric/`, so don't commit those changes. `up.py` deploys CouchDB. For LevelDB, replace the `up.py` step with `blockchain/scripts/ensure-networks.sh` and `STATE_DB="-s goleveldb" blockchain/scripts/deploy.sh --orgs 5` (use `deploy.sh up` for 3 organisations).

**Feed samples:** this sends statements through the whole pipeline and needs the model weights:

```bash
python3 benchmarks/feed_samples.py --samples 6
```

The votes are scripted, so this shows that the path completes and does not measure accuracy.

The Caliper results used in the report are in [`report_results/caliper/`](./report_results/caliper/). `report_results/final_data_cleaned_v1.csv` holds the September feed runs from the first draft, from an older contract and with half the labels random.

## Unit tests

There are 146 tests: 39 chaincode, 81 gateway, 20 across the other Python services and 6 in the Fabric sidecar.

```bash
# chaincode
(cd blockchain/chaincode/misinformation/go && go test ./...)

# Python services: use a virtual environment
python3 -m venv .venv && . .venv/bin/activate
pip install pytest pytest-asyncio \
  -r "apps/blockchain_gateway/app/v1-0-0/api/requirements.txt" \
  -r apps/submission-worker/requirements.txt \
  -r apps/claim-ingest-worker/requirements.txt \
  -r apps/fact-checking-service/requirements.txt \
  -r apps/ipfs_gateway/requirements.txt \
  aiokafka==0.12.0
for d in "apps/blockchain_gateway/app/v1-0-0/api" apps/submission-worker \
  apps/claim-ingest-worker apps/fact-checking-service apps/ipfs_gateway \
  apps/flagging-engine; do
  (cd "$d" && python -m pytest -q tests)
done

# Fabric sidecar
(cd apps/fabric_gateway && npm install && npm test)
```
