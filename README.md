A Blockchain Enabled Framework For Misinformation Monitoring 

Steps:

1.) blockchain/scripts/start-ipfs.sh
2.) venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring/bin/uvicorn --app-dir apps/ai_service/app/v1-0-0/api/ server:app --host 0.0.0.0 --port 8000
3.) ./startup.sh --orgs 3 --samples 50
4.) docker compose -f blockchain/explorer/docker-compose.yaml down -v \ docker compose -f blockchain/explorer/docker-compose.yaml up -d
5.) docker compose -f blockchain/explorer/docker-compose.yaml up -d
6.) curl -s -H "X-API-Key: stress-key" http://localhost:8000/api/reports/<CID> 

flowchart TB
    subgraph AI["AI / DATA PIPELINE (offline, Python)"]
        A1["data_prep.py<br/>load Twitter dataset,<br/>clean + anonymize,<br/>OpenAI gpt-4o-mini<br/>batch translation (eng-nso)"]
        A2["data_parser.py<br/>merge translations,<br/>build real-world<br/>training set"]
        A3["fine_tuning_training.ipynb<br/>model fine-tuning"]
        A4["Model inference:<br/>label (0/1) + confidence<br/>for each report"]
    end

    subgraph GW["REST GATEWAY (FastAPI)"]
        B1["server.py<br/>org onboarding, admission voting,<br/>report submit/vote/finalize/verify"]
        B2["storage.py<br/>OffChainStore (SQLite: org_keys,<br/>reports, report_index)"]
        B3["report.py<br/>canonical JSON + SHA-256<br/>content hash"]
        B4["blockchain.py FabricBridge<br/>invokes hyperledger peer CLI"]
    end

    subgraph OFF["OFF-CHAIN STORAGE"]
        C1["IPFS node (ipfs/kubo Docker)<br/>:5001 API, content-addressed CID<br/>= report_id"]
        C2["SQLite offchain.db<br/>(fallback + index)"]
    end

    subgraph FAB["HYPERLEDGER FABRIC NETWORK"]
        D1["Org1 / Org2 / Org3 peers<br/>endorsement policy OutOf(2, ...)<br/>CouchDB state DB"]
        D2["Orderer (Raft)"]
        D3["Chaincode misinformation.go<br/>RegisterOrg, RequestOrgAdmission,<br/>SubmitReport, CastVote,<br/>FinalizeReport, ExpireReport,<br/>Query*/History — 72h voting window,<br/>2/3 quorum"]
        D4["Blockchain Explorer<br/>(docker-compose)"]
    end

    LD["benchmarks/load-http.py<br/>concurrent load driver (X-API-Key)"]

    A1 --> A2 --> A3 --> A4
    A4 -->|"report (label, confidence)"| B1
    LD -->|"HTTP stress"| B1
    B1 --> B2
    B2 <-->|"ipfs add/cat"| C1
    B2 <--> C2
    B1 -->|"content_hash, metadata"| B4
    B4 <-->|"peer chaincode invoke/query"| D1
    D1 <--> D3
    D1 --> D2
    D3 --> D4
























# public IPFS gateway (no API key; -L for the CIDv1 redirect)
# cross-machine proof: any node dials 12D3KooWAZBjyLbW54RxhacXb1hTdNZN83ps9VoLU4goR8HVhgEW @ 154.117.189.250:4001 (ipfs get <CID>)
    
LIST:
7. blockchain/scripts/start-ipfs.sh                                 X
8. blockchain/scripts/bootstrap-keys.sh                             X
9. apps/ai_service/app/v1-0-0/api/server.py                         
10. apps/ai_service/app/v1-0-0/api/storage.py
11. apps/ai_service/app/v1-0-0/src/report.py
12. apps/ai_service/app/v1-0-0/src/blockchain.py

14. startup.sh
15. blockchain/scripts/deploy.sh
17. blockchain/chaincode/misinformation/go/misinformation.go
19. blockchain/scripts/onboard-org3.sh
#20. blockchain/scripts/add-orgs.sh
21. blockchain/scripts/register-orgs.sh
22. blockchain/scripts/gen-explorer-config.sh

23. benchmarks/load-http.py

26. blockchain/explorer/docker-compose.yaml
27. blockchain/explorer/config.json
28. blockchain/explorer/connection-profile/networkConfig.json