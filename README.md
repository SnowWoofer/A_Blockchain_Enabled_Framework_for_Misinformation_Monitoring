A Blockchain Enabled Framework For Misinformation Monitoring 

Steps:

1.) blockchain/scripts/start-ipfs.sh
2.) venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring/bin/uvicorn --app-dir apps/ai_service/app/v1-0-0/api/ server:app --host 0.0.0.0 --port 8000
3.) ./startup.sh --orgs 3 --samples 50
4.) docker compose -f blockchain/explorer/docker-compose.yaml down -v \ docker compose -f blockchain/explorer/docker-compose.yaml up -d
5.) docker compose -f blockchain/explorer/docker-compose.yaml up -d
6.) curl -s -H "X-API-Key: stress-key" http://localhost:8000/api/reports/<CID> 
























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