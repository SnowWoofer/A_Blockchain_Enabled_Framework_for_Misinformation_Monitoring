import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.kafka_consumer import FlaggingConsumer
from app.config import settings


class Consumer:
    def __init__(self,messages,events):self.messages=iter(messages);self.events=events
    def __aiter__(self):return self
    async def __anext__(self):
        try:return next(self.messages)
        except StopIteration:raise StopAsyncIteration
    async def commit(self,offsets):self.events.append('commit')


def test_failed_publish_retries_before_dead_letter_and_offset(tmp_path,monkeypatch):
    events=[];worker=FlaggingConsumer(None,'test')
    worker.consumer=Consumer([SimpleNamespace(value=b'{}',topic='claims.raw',partition=0,offset=0)],events)
    monkeypatch.setattr(settings,'dead_letter_path',str(tmp_path/'dlq'))
    async def handle(payload):events.append('publish');raise OSError('broker down')
    async def no_sleep(_):pass
    worker._handle=handle;monkeypatch.setattr(asyncio,'sleep',no_sleep)
    original=worker._dead_letter
    def dead(payload,error):original(payload,error);events.append('durable_dlq')
    worker._dead_letter=dead
    asyncio.run(worker._run())
    assert events==['publish','publish','publish','durable_dlq','commit']


def test_failed_dlq_prevents_commit(tmp_path,monkeypatch):
    events=[];worker=FlaggingConsumer(None,'test')
    worker.consumer=Consumer([SimpleNamespace(value=b'broken',topic='claims.raw',partition=0,offset=0)],events)
    def dead(*args):raise OSError('disk full')
    worker._dead_letter=dead
    with pytest.raises(OSError):asyncio.run(worker._run())
    assert events==[]
