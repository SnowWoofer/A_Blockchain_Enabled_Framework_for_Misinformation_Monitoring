from pathlib import Path
import sys
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.main import app,state
from app.config import settings


@pytest.fixture
def ingest(monkeypatch):
    monkeypatch.setattr(settings,'benchmark_message_ids',False)
    state['producer']=AsyncMock()
    yield TestClient(app),state['producer']
    state.clear()


def test_normal_ingestion_keeps_server_generated_message_ids(ingest):
    client,producer=ingest
    result=client.post('/ingest',json={'claims':['exact sample text']})
    assert result.status_code==200
    row=producer.send_and_wait.call_args.args[1]
    assert row['content']=='exact sample text'
    assert row['msg_id']==result.json()['msg_ids'][0] and row['msg_id'].startswith('test_')


def test_benchmark_message_ids_are_disabled_by_default(ingest):
    client,producer=ingest
    result=client.post('/ingest',json={'claims':['sample'],'msg_ids':['planned']})
    assert result.status_code==400
    producer.send_and_wait.assert_not_called()


def test_benchmark_ids_preserve_exact_text_without_org_selector(ingest,monkeypatch):
    client,producer=ingest;monkeypatch.setattr(settings,'benchmark_message_ids',True)
    result=client.post('/ingest',json={'claims':['a','b'],'msg_ids':['first','second']})
    assert result.status_code==200 and result.json()['msg_ids']==['first','second']
    rows=[call.args[1] for call in producer.send_and_wait.call_args_list]
    assert [(row['msg_id'],row['content']) for row in rows]==[('first','a'),('second','b')]
    assert all(set(row)=={'msg_id','content','source_platform','ingest_timestamp'} for row in rows)


@pytest.mark.parametrize('ids',[[],['one'],['same','same'],['','two'],['a'*257,'two']])
def test_invalid_benchmark_ids_publish_nothing(ingest,monkeypatch,ids):
    client,producer=ingest;monkeypatch.setattr(settings,'benchmark_message_ids',True)
    assert client.post('/ingest',json={'claims':['a','b'],'msg_ids':ids}).status_code==422
    producer.send_and_wait.assert_not_called()
