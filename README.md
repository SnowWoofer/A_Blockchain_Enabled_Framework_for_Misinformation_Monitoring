# A Blockchain Enabled Framework For Misinformation Monitoring

Consortium blockchain framework for monitoring misinformation: a Hyperledger
Fabric network anchors only a SHA-256 hash + off-chain URI of each report on the
ledger, while the full report object (including raw text) lives off-chain in
IPFS (SQLite fallback). A FastAPI gateway authenticates orgs by API key and signs
on their behalf.

## Architecture

```
┌──────────┐   ┌───────────────────────┐   ┌──────────────────────────┐
│ kubos IPFS │   │  FastAPI gateway :8000 │   │  Hyperledger Fabric       │
│ (off-chain) │   │  (server.py / storage) │   │  test-network (org1..N)   │
└────┬─────┘   └──────────┬────────────┘   └────────────┬─────────────┘
     └── CID (=report_id) │ X-API-Key auth              │ peer CLI + crypto
                          │  content-hash + URI ────────►│ chaincode (Go)
                          └──────────────┘               └─────────────┘
```

- **On-chain** (chaincode): hash + `off_chain_uri` + metadata only. Never raw text.
- **Off-chain**: full report in IPFS (content-addressed; CID doubles as the
  report id), SQLite fallback.
- **Explorer** (`:8080`): network visualizer over the `fabric_test` network.

## Dependencies

### Language / framework
- Go 1.22.0 (chaincode)
- Hyperledger Fabric test network (via `fabric-samples/`)
- Python 3 + FastAPI/uvicorn (API gateway, see `apps/ai_service/app/v1-0-0/api/requirements.txt`)

### Go modules — chaincode (`blockchain/chaincode/misinformation/go/go.mod`)
- `hyperledger/fabric-chaincode-go/v2 v2.0.0`
- `hyperledger/fabric-contract-api-go/v2 v2.2.0`
- `hyperledger/fabric-protos-go-apiv2 v0.3.4`
- `google.golang.org/protobuf v1.36.1`
- indirect: `go-openapi/*`, `xeipuuv/gojsonschema`, `golang.org/x/{net,sys,text}`,
  `google.golang.org/grpc v1.67.0`, `gopkg.in/yaml.v3`, etc. (vendored in `vendor/`)

### Tooling / runtime
- Docker + Docker Compose (v2 plugin; legacy v1.29.2 breaks the test network)
- `jq`, `python3`, `curl`
- Fabric binaries in `blockchain/fabric-samples/bin`: `peer`, `orderer`,
  `fabric-ca-client`, `cryptogen`, `configtxgen`, `configtxlator`, `discover`,
  `ledgerutil`, `osnadmin`, `fabric-ca-server`

### Docker images
- `hyperledger/fabric-peer`, `hyperledger/fabric-orderer`, `hyperledger/fabric-ca`
- `hyperledger/fabric-tools`
- `couchdb` (default state DB)
- `hyperledger/explorer:1.1.1`, `hyperledger/explorer-db:latest`
- `ipfs/kubo:latest` (IPFS node: RPC `5001`, gateway `8081`, swarm `4001` tcp+udp)

## Steps

```bash
# 1) Off-chain content store
blockchain/scripts/start-ipfs.sh

# 2) API gateway (auth: X-API-Key; keys minted via bootstrap-keys.sh)
venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring/bin/uvicorn \
  --app-dir apps/ai_service/app/v1-0-0/api/ server:app --host 0.0.0.0 --port 8000

# 3) Deploy network + chaincode + load (orgs = founding org count, samples = load size)
./startup.sh --orgs 3 --samples 50

# 4) Recreate Explorer so it picks up the regenerated connection profile
docker compose -f blockchain/explorer/docker-compose.yaml down -v
docker compose -f blockchain/explorer/docker-compose.yaml up -d

# 5) Verify a report CID (tamper-evidence: off-chain hash == on-chain hash)
curl -s -H "X-API-Key: stress-key" http://localhost:8000/api/reports/<CID>
curl -s -H "X-API-Key: stress-key" http://localhost:8000/api/reports/<CID>/verify
```

Explorer UI: http://localhost:8080 (login `exploreradmin` / `exploreradminpw`).
