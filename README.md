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
traffic. See **[SECURITY.md](./SECURITY.md)** for the threat model, the
controls and the known limitations.

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

Output is mirrored to `logs/up-<timestamp>.log`. The sections below describe
the same steps individually.

> **Before the first run**, download the model weights (`model.safetensors`, 2.2 GB,
> not in git) from the [model folder on Google Drive](https://drive.google.com/drive/folders/1EcaSIEqh1-BTeSL_ThZse6P1IAhImncs?usp=sharing) and place them in
> `apps/flagging-engine/model/`, or the flagging engine is not started. If you hit problems on **Docker Engine 29.x**,
> see [Docker 29 / WSL 2](#docker-29--wsl-2) below.
|