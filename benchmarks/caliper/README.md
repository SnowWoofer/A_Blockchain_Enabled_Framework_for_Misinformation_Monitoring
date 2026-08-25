# Caliper Benchmark Suite

Hyperledger [Caliper](https://hyperledger.github.io/caliper/) benchmarks for the
`misinformation` chaincode (channel `mychannel`), bound to the **official Fabric
Gateway SDK** (`fabric:fabric-gateway`) — the same SDK the API sidecar uses.

## Layout

```
benchmarks/caliper/
├── Dockerfile                              # caliper-cli 0.7.1 + fabric:fabric-gateway bind
├── docker-compose.yaml                     # runs on the fabric_test network, crypto at /crypto:ro
├── run-caliper.sh                          # build + run + report location
├── gen-caliper-config.sh                   # regenerate ccp.json for --orgs N
├── benchmarks/misinformation-benchmark.yaml # rounds / rates / workers
├── networks/fabric/test-network.yaml       # Caliper network config (Org1 identity)
├── networks/fabric/ccp.json                # connection profile (peer0.org1..3)
└── workload/
    ├── submitReport.js                     # SubmitReport with valid synthetic args
    └── queryAllReports.js                  # read-only world-state range scan
```

## Run

```bash
./startup.sh --orgs 3 --samples 50   # network must be up first
benchmarks/caliper/run-caliper.sh
```

Report lands at `benchmarks/caliper/report.html`.

## Rounds

| Round | Contract function | Load | Notes |
|---|---|---|---|
| `submit-report-write` | `SubmitReport` | 500 tx @ fixed 25 TPS, 2 workers | 2-of-3 endorsement; unique report ids per worker/tx |
| `query-all-reports-read` | `QueryAllReports` | 1000 tx @ fixed 50 TPS, 2 workers | CouchDB-backed world-state scan |

Workload args match the chaincode validators exactly (64-hex content hash,
confidence in [0,1], RFC3339 timestamp), so every tx is a fair, accepted write.

## Scaling the consortium

After adding orgs beyond org3:

```bash
benchmarks/caliper/gen-caliper-config.sh --orgs N
```

then extend `networks/fabric/test-network.yaml` with the extra organizations
(each org's Admin certificate is already covered by the generated profile).

## Requirements

- Docker + Compose v2 and a deployed test network (`fabric_test` network present).
- No local Node/npm install needed — everything builds inside the image.
