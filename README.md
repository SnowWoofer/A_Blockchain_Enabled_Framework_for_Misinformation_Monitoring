# A Blockchain-Enabled Framework for Misinformation Monitoring

A research prototype for monitoring election misinformation. A multilingual AI
classifier (AfroXLMR, for English, Sepedi and isiZulu) flags claims. Independent
fact-checking organisations review them on a permissioned Hyperledger Fabric
ledger, and the claim text and evidence are kept off-chain in a private IPFS swarm.

Everything runs on **one host**. Each organisation is simulated with its own
peer, signer and role identities. This is a research prototype, not a production
system; see [Research boundaries](#research-boundaries).

---

## 1. Requirements

| Requirement | Version / note |
|---|---|
| OS | Linux, or Windows with WSL 2 |
| Docker | Docker Engine with Compose v2 (`docker compose version`) |
| Python | 3.12 |
| Go | 1.22 or newer (builds the chaincode) |
| Node.js | 20 or newer. Only used by the regression syntax checks; Caliper runs in Docker. |
| Memory | About 10 GB free for a 3-organisation network plus the AI feed |
| Network | Needed for the first install and the first Docker builds |

---

## 2. Install (once)

Run every command from the repository root unless a step says otherwise.

### 2.1 Clone and create the Python environment

```bash
git clone https://github.com/SnowWoofer/A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring.git
cd A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r blockchain/scripts/requirements.txt
```

Keep the environment **activated** (`source .venv/bin/activate`) in every new
terminal. The scripts use the active `python3` and stop with a clear message if
the project packages are missing. To use a different interpreter, set
`MISINFO_PYTHON=/path/to/python`.

### 2.2 Hyperledger Fabric 2.5.16 tools

The deployment script needs Fabric's `cryptogen`, `configtxgen` and `osnadmin`
in `blockchain/fabric-samples/bin/`, and Fabric's default configuration in
`blockchain/fabric-samples/config/`. Download them with the official installer:

```bash
mkdir -p blockchain/fabric-samples && cd blockchain/fabric-samples
curl -sSLO https://raw.githubusercontent.com/hyperledger/fabric/main/scripts/install-fabric.sh
chmod +x install-fabric.sh
./install-fabric.sh --fabric-version 2.5.16 binary
cd ../..
ls blockchain/fabric-samples/bin   # should list cryptogen, configtxgen, osnadmin, peer, ...
```

### 2.3 Chaincode dependencies

The chaincode is built with vendored Go modules:

```bash
(cd blockchain/chaincode/misinformation/go && go mod vendor)
```

### 2.4 Model weights

The fine-tuned AfroXLMR-large-76L checkpoint (fully fine-tuned, seed 619) is too
large for git. The tokenizer and configuration files are already in
`apps/flagging-engine/model/`; download the weights file next to them:

```bash
curl -L -o apps/flagging-engine/model/model.safetensors "<MODEL_WEIGHTS_URL>"
sha256sum apps/flagging-engine/model/model.safetensors
# expected: 9517cf3ea09271b39b84c8395a5814cb48d1ee747b6a132f84fd4198fe383725
```

The same digest appears as `model_version` (`checkpoint:9517cf3e…`) in every feed result.

### 2.5 Docker images

```bash
docker build -t misinfo-gateway:latest apps/fabric_gateway
docker build -t misinfo-blockchain-gateway:latest apps/blockchain_gateway
docker build -t misinfo-ipfs-gateway:latest apps/ipfs_gateway
docker pull hyperledger/fabric-peer:2.5.16
docker pull hyperledger/fabric-orderer:2.5.16
docker pull ipfs/kubo:latest
docker pull couchdb:3.4.2
docker pull apache/kafka:3.9.1
```

The Caliper and Explorer images are built automatically the first time you use them.

---

## 3. Quick start

### 3.1 Start a network

```bash
./startup.sh --orgs 3 --database leveldb
```

This creates a private Fabric network:
- one Raft orderer
- per organisation: a peer, a signer and role identities
- three IPFS nodes with storage bridges
- the REST API

It then runs a smoke test (submit a claim, majority review, notes, permission
checks, evidence verification) and finishes with `Ready. Stop this deployment with: …`.

| Option | Meaning | Default |
|---|---|---|
| `--name NAME` | Deployment label (lowercase letters, digits, hyphens; max 40) | `development` |
| `--orgs N` | Number of organisations (at least 3) | `3` |
| `--database leveldb\|couchdb` | Peer state database | `leveldb` |

Re-running `./startup.sh` with an existing name restarts that deployment with its
data. Use a new name for a fresh network. The API listens on
`https://127.0.0.1:18000` (interactive docs at `/docs`); use
`python blockchain/scripts/deployment.py up --port-base PORT` to choose another port block.

### 3.2 Benchmark 1: the full feed workflow

```bash
python benchmarks/pipeline.py --samples 100 --seed 100
```

This runs the real path end to end:
1. ingestion
2. Kafka
3. the AI classifier (CPU, int8)
4. the REST API, IPFS and Fabric
5. scripted majority reviews
6. evidence verification

A successful run ends with `Feed PASSED`.

| Option | Meaning |
|---|---|
| `--name NAME` | Deployment to use (default `development`) |
| `--samples N` | Number of test-set records (`6` = balanced six; `0` = whole dataset) |
| `--seed S` | Selection seed (same seed, count and data give the same selection) |
| `--random P` | Submit P% of samples as randomly chosen organisations other than Org1 |
| `--rejection-percent P` | Script the reviewers to overturn the AI label on P% of samples (a workflow demonstration, not an accuracy result). Omit it to review with the dataset labels. |

Reviewers are each report's frozen eligible organisations (everyone except the
submitter). A label needs floor(E/2) + 1 matching votes. Votes are scripted; they
don't measure human review time or real-world truth.

### 3.3 Benchmark 2: Caliper reads and writes

```bash
benchmarks/caliper/run-caliper.sh --count 1000 --workers 2 --load 100
```

This uses official Hyperledger Caliper 0.7.1 with the Fabric Gateway connector.
It runs N committed `Submit` writes, then N full-scan `QueryAllReports` reads,
directly against the ledger, with no REST, IPFS, AI or review costs.

| Option | Meaning | Default |
|---|---|---|
| `--count N` | Write attempts, then the same number of read attempts | `1000` |
| `--workers W` | Client processes sharing each round | `2` |
| `--load L` | Target pending transactions (fixed-load controller; not a fixed TPS) | `100` |
| `--name NAME` | Deployment to use | `development` |

For a quick functional check, use `--count 6 --workers 2 --load 2`. Run the feed
and Caliper one after the other, not at the same time.

### 3.4 Inspect the ledger (Hyperledger Explorer, optional)

```bash
blockchain/explorer/manage.sh up
blockchain/explorer/manage.sh credentials
```

Open `http://localhost:18080`, choose network `development` and log in with the
printed credentials. Add `--name NAME` for another deployment. To stop it, run
`blockchain/explorer/manage.sh down`. Stop Explorer before taking measurements.

### 3.5 Status, logs and stop

```bash
python blockchain/scripts/deployment.py status
python blockchain/scripts/deployment.py logs --services api signer1
python blockchain/scripts/deployment.py down
```

`down` stops the containers but keeps the deployment's volumes and keys.

### 3.6 Where results go

Every run writes a new folder under `results/repair/<name>/`, containing
`logs.txt` and structured results:
- feed: `result.json`
- Caliper: `result.json`, `report.html`, per-transaction records

Existing runs are never overwritten. Private state (keys, credentials) lives under
`blockchain/runtime/<name>/`. **Never publish that folder.**

---

## 4. Tests

With the environment activated:

```bash
# Unit tests
(cd blockchain/chaincode/misinformation/go && go test -mod=vendor ./...)
python -m pytest -q -p no:cacheprovider apps/blockchain_gateway/app/v1-0-0/api/tests
python -m pytest -q -p no:cacheprovider benchmarks/tests
python -m pytest -q -p no:cacheprovider blockchain/explorer/tests
python -m pytest -q -p no:cacheprovider apps/submission-worker/tests
python -m pytest -q -p no:cacheprovider apps/claim-ingest-worker/tests
python -m pytest -q -p no:cacheprovider apps/flagging-engine/tests

# All of the above plus syntax checks, with a saved summary
python -c 'from benchmarks.regression import main; raise SystemExit(main())'

# Full functional acceptance run on fresh, disposable deployments
python -m benchmarks.test_all --plan    # preview the steps
python -m benchmarks.test_all
```

The full run does the following, then stops only its own containers:
- creates a fresh deployment
- runs regression and live API and permission checks
- runs several feeds, including mixed submitters and scripted overturns
- checks TLS and Kafka mutual TLS
- runs small Caliper rounds
- starts Explorer and restarts it
- checks ledger persistence
- checks retention and credential revocation on a separate network

It takes a while and needs the memory listed in [Requirements](#1-requirements).

---

## 5. Configuration and behaviour

### Roles

Each organisation has three certificate-bound roles. The role is checked in the
chaincode, and again at the API and the organisation's signer.

| Role | Can do |
|---|---|
| Official | Found the consortium; request, vote on and finalise organisation admission; expire overdue reports. **Cannot** submit claims, vote on reports or add notes. |
| Fact-checker | Submit claims; review reports when its organisation is an eligible independent reviewer; add notes to its own organisation's reports |
| Observer | Read only |

API calls use a provisioned `X-API-Key` header (one key per organisation and role).
The gateway stores only SHA-256 digests of the keys. Revoke a key with
`python blockchain/scripts/credentials.py --name NAME --org org2 --role fact_checker`.

### Report lifecycle

**submit → independent votes → automatic outcome.**
- The deciding majority vote sets `FINAL`.
- If every eligible organisation has voted with no majority, the report becomes `DISPUTED`.
- Unresolved reports expire after 72 hours.
- Notes (`POST /api/reports/{id}/notes`) are append-only and never change a verdict.
- `GET /api/reports/{id}/verify` checks the claim, review evidence, notes and ledger
  history against their on-chain commitments. It checks integrity, not factual truth.

`GET /api/public/reports` and `GET /api/public/reports/{id}` publish `FINAL`
verdict metadata without a credential. Claim text, notes and CIDs stay behind
authentication. For a scripted walkthrough of every route, run
`python -m benchmarks.api_check --name NAME`.

### Data handling and retention

- **On-chain:** content hashes, CIDs and workflow metadata (labels, votes, reviewer set, timestamps), never claim text.
- **Off-chain:** claim text and evidence, held in a private three-node IPFS swarm.
- Off-chain objects have a 30-day retention deadline.
  `python blockchain/scripts/retention.py --name NAME` dry-runs by default. With `--execute --reason "…"`
  it unpins expired objects from the managed IPFS nodes, garbage-collects them and logs each deletion.
  On-chain commitments remain.

### Transport security

Startup generates a private CA per deployment. The following all use TLS, and
clients verify the certificate chain and hostname:
- the API, the signers and the storage bridges (HTTPS)
- Kafka, with mutual TLS
- Fabric peers, the orderer and the chaincode

Call the API with the CA:
`curl --cacert blockchain/runtime/development/transport/ca.crt https://127.0.0.1:18000/health`.
TLS doesn't encrypt data at rest. IPFS content, Kafka logs and the ledger are
stored unencrypted on the host.

---

## 6. Repository layout

| Path | Contents |
|---|---|
| `startup.sh` | Main entry point (deploy plus smoke test) |
| `blockchain/scripts/` | Deployment, TLS, credentials, retention |
| `blockchain/chaincode/misinformation/go/` | The smart contract and its tests |
| `blockchain/explorer/` | Hyperledger Explorer launcher |
| `apps/blockchain_gateway/` | REST API (FastAPI) and its tests |
| `apps/fabric_gateway/` | Per-organisation signer (Fabric Gateway SDK) |
| `apps/ipfs_gateway/` | Storage bridge to IPFS |
| `apps/claim-ingest-worker/`, `apps/flagging-engine/`, `apps/submission-worker/` | AI pipeline: ingest → classify → submit |
| `apps/fact-checking-service/` | Reviewer front-end (implemented; not used by the benchmarks) |
| `benchmarks/` | Feed benchmark, Caliper runner, test runners and checks |
| `data/translated_masked_samples_test.json` | Held-out synthetic test set used by the feed |

---

## 7. Results

The benchmark evidence used in the report will be linked here.

---

## Research boundaries

- Single host and a **single Raft orderer** (no ordering failover). Endorsement is a fixed 2-of-N peer policy.
- Organisations are simulated on one machine. Reviewers are **scripted**, and the inputs are **synthetic**.
- Certificates are issued by the deployment script (no Fabric CA, no certificate revocation list). Identity is per organisation and role, not per person.
- A majority vote or a hash proves integrity and agreement, not factual truth.
- Production security, institutional independence and legal (POPIA) compliance have not been established.
