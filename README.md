A Blockchain Enabled Framework For Misinformation Monitoring 

Steps:

1.) blockchain/scripts/start-ipfs.sh
2.) venv_A_Blockchain_Enabled_Framework_for_Misinformation_Monitoring/bin/uvicorn --app-dir apps/ai_service/app/v1-0-0/api/ server:app --host 0.0.0.0 --port 8000
3.) ./startup.sh --orgs 3 --samples 50
4.) docker compose -f blockchain/explorer/docker-compose.yaml down -v \ docker compose -f blockchain/explorer/docker-compose.yaml up -d
5.) docker compose -f blockchain/explorer/docker-compose.yaml up -d
6.) curl -s -H "X-API-Key: stress-key" http://localhost:8000/api/reports/<CID> 