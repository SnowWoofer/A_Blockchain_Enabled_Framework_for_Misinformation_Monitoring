import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import httpx
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.consumer import SubmissionConsumer
from app.config import settings


def message(value,offset=0):return SimpleNamespace(value=value,topic='claims.flagged',partition=0,offset=offset)


class Consumer:
    def __init__(self,messages,events):self.messages=iter(messages);self.events=events
    def __aiter__(self):return self
    async def __anext__(self):
        try:return next(self.messages)
        except StopIteration:raise StopAsyncIteration
    async def commit(self,offsets):self.events.append(('commit',next(iter(offsets.values()))))


def test_offsets_follow_durable_handling_and_poison_does_not_block(tmp_path,monkeypatch):
    worker=SubmissionConsumer();events=[]
    monkeypatch.setattr(settings,'output_path',str(tmp_path/'dlq'))
    original=worker._dead_letter
    def dlq(payload,error):original(payload,error);events.append(('durable_dlq',None))
    worker._dead_letter=dlq
    async def submit(payload):events.append(('submit',payload['msg_id']))
    worker._submit=submit
    worker.consumer=Consumer([message(b'broken'),message(b'{"msg_id":"later"}',1)],events)
    asyncio.run(worker._run())
    assert events==[('durable_dlq',None),('commit',1),('submit','later'),('commit',2)]
    assert json.loads((tmp_path/'dlq').read_text())['_error']=='invalid JSON'


def test_failed_dead_letter_does_not_commit_or_skip_later_offsets():
    worker=SubmissionConsumer();events=[]
    worker.consumer=Consumer([message(b'broken'),message(b'{}',1)],events)
    def failed(*args):raise OSError('disk unavailable')
    worker._dead_letter=failed
    with pytest.raises(OSError):asyncio.run(worker._run())
    assert events==[]


def test_commit_failure_stops_consuming():
    worker=SubmissionConsumer();events=[]
    worker.consumer=Consumer([message(b'{"msg_id":"one"}'),message(b'{"msg_id":"two"}',1)],events)
    async def submit(payload):events.append(payload['msg_id'])
    async def commit(offsets):raise RuntimeError('broker unavailable')
    worker._submit=submit;worker.consumer.commit=commit
    with pytest.raises(RuntimeError):asyncio.run(worker._run())
    assert events==['one']


def payload():return dict(msg_id='same',confidence=0.0,model_version='test',content='synthetic',source_platform='test',flagged=False)


def test_retry_is_same_message_and_zero_confidence_is_valid(tmp_path,monkeypatch):
    seen=[]
    async def no_sleep(_):pass
    monkeypatch.setattr(asyncio,'sleep',no_sleep)
    monkeypatch.setattr(settings,'output_path',str(tmp_path/'dlq'))
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(503 if len(seen)<3 else 200,json={'ok':True})
    async def run():
        worker=SubmissionConsumer()
        async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(handler)) as worker.http:
            await worker._submit(payload())
    asyncio.run(run())
    assert len(seen)==3 and all(r==seen[0] for r in seen)
    assert seen[0]['confidence']==0.0 and not (tmp_path/'dlq').exists()


def test_permanent_error_has_one_attempt_and_durable_dlq(tmp_path,monkeypatch):
    seen=[];monkeypatch.setattr(settings,'output_path',str(tmp_path/'dlq'))
    def handler(request):seen.append(1);return httpx.Response(422,json={'detail':'invalid'})
    async def run():
        worker=SubmissionConsumer()
        async with httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(handler)) as worker.http:
            await worker._submit(payload())
    asyncio.run(run())
    assert len(seen)==1 and json.loads((tmp_path/'dlq').read_text())['msg_id']=='same'


def routing_file(tmp_path,org='org3'):
    import hashlib
    spec={'credentials':{org:'other-org-test-key-'+'x'*32},'messages':{'same':{
        'org':org,'source_platform':'test','content_sha256':hashlib.sha256(b'synthetic').hexdigest()}}}
    path=tmp_path/'routes.json';path.write_text(json.dumps(spec));path.chmod(0o600)
    return path,spec


def test_private_route_selects_actual_credential_and_preserves_it_on_retry(tmp_path,monkeypatch):
    path,spec=routing_file(tmp_path);seen=[]
    monkeypatch.setattr(settings,'output_path',str(tmp_path/'dlq'))
    async def no_sleep(_):pass
    monkeypatch.setattr(asyncio,'sleep',no_sleep)
    def handler(request):
        seen.append((request.headers['X-API-Key'],json.loads(request.content)))
        return httpx.Response(503 if len(seen)==1 else 200,json={'ok':True})
    async def run():
        worker=SubmissionConsumer();worker._load_benchmark_routes(path)
        async with httpx.AsyncClient(base_url='https://test',headers={'X-API-Key':'org1-default'},transport=httpx.MockTransport(handler)) as worker.http:
            await worker._submit(payload()|{'submitter_org':'org99'})
            await worker._submit(payload()|{'msg_id':'unplanned','submitter_org':'org3'})
    asyncio.run(run())
    assert [key for key,_ in seen]==[spec['credentials']['org3'],spec['credentials']['org3'],'org1-default']
    assert seen[0][1]==seen[1][1] and seen[0][1]['content']=='synthetic'
    assert all('submitter_org' not in body for _,body in seen)


@pytest.mark.parametrize('change',[{'content':'tampered'},{'source_platform':'other'},{'msg_id':[]}])
def test_route_mismatch_is_dead_lettered_without_any_gateway_request(tmp_path,monkeypatch,change):
    path,_=routing_file(tmp_path);seen=[]
    monkeypatch.setattr(settings,'output_path',str(tmp_path/'dlq'))
    def handler(request):seen.append(request);return httpx.Response(200)
    async def run():
        worker=SubmissionConsumer();worker._load_benchmark_routes(path)
        async with httpx.AsyncClient(base_url='https://test',transport=httpx.MockTransport(handler)) as worker.http:
            await worker._submit(payload()|change)
    asyncio.run(run())
    assert not seen and (tmp_path/'dlq').exists()


def test_routed_failure_keeps_submitter_hint_but_never_key(tmp_path,monkeypatch):
    path,spec=routing_file(tmp_path);monkeypatch.setattr(settings,'output_path',str(tmp_path/'dlq'))
    async def run():
        worker=SubmissionConsumer();worker._load_benchmark_routes(path)
        async with httpx.AsyncClient(base_url='https://test',transport=httpx.MockTransport(lambda request:httpx.Response(422))) as worker.http:
            await worker._submit(payload()|{'_submission_org':'org99'})
    asyncio.run(run())
    text=(tmp_path/'dlq').read_text()
    assert json.loads(text)['_submission_org']=='org3'
    assert spec['credentials']['org3'] not in text


def test_world_readable_routing_file_refused(tmp_path):
    path,_=routing_file(tmp_path);path.chmod(0o644)
    with pytest.raises(ValueError,match='private'):SubmissionConsumer()._load_benchmark_routes(path)
