import hashlib
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))


@pytest.fixture
def client(tmp_path,monkeypatch):
    import api_v3
    from fastapi.testclient import TestClient
    keys={(f'org{i}',role):hashlib.sha256(f'{i}-{role}-test-fixture'.encode()).hexdigest()
          for i in range(1,4) for role in ('official','fact_checker','observer')}
    credentials=tmp_path/'credentials.json'
    credentials.write_text(json.dumps([{'org':o,'role':r,'api_key':k} for (o,r),k in keys.items()]))
    monkeypatch.setenv('CREDENTIALS_FILE',str(credentials))
    public_credential=tmp_path/'public-observer.json'
    public_credential.write_text(json.dumps({'org':'org1','role':'observer','api_key':keys['org1','observer']}))
    monkeypatch.setenv('PUBLIC_OBSERVER_CREDENTIAL_FILE',str(public_credential))
    monkeypatch.setenv('OFFCHAIN_DB',str(tmp_path/'objects.db'))
    monkeypatch.setenv('REFERENCE_DB',str(tmp_path/'ledger.db'))
    monkeypatch.setenv('STORAGE_MODE','sqlite-development')
    monkeypatch.setenv('LEDGER_BACKEND','sqlite-reference')
    monkeypatch.setenv('ORGS','org1,org2,org3')
    monkeypatch.setenv('ENABLE_EXPIRY','false')
    with TestClient(api_v3.app) as c:
        c.keys=keys;c.store=api_v3.STORE;c.ref=api_v3.REFERENCE
        yield c
