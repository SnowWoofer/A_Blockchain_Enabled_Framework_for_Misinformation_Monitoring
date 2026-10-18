#!/usr/bin/env python3
"""Replay scored dead letters; retain input and record a separate replay log."""
import argparse
import asyncio
import base64
import datetime as dt
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import http


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True);p.add_argument('--output',required=True)
    p.add_argument('--stage',choices=['scored','raw'],default='scored')
    p.add_argument('--credentials');p.add_argument('--org',default='org1');p.add_argument('--api',default='http://127.0.0.1:18000')
    p.add_argument('--broker',default='localhost:18004');p.add_argument('--topic',default='claims.raw')
    a=p.parse_args();key=None
    if a.stage=='scored':
        if not a.credentials:p.error('scored replay requires --credentials')
        creds=json.loads(Path(a.credentials).read_text());key=next(r['api_key'] for r in creds if r['org']==a.org and r['role']=='fact_checker')
    failed=0
    with Path(a.output).open('x') as result:
        for number,line in enumerate(Path(a.input).read_text().splitlines(),1):
            try:
                row=json.loads(line)
                if a.stage=='raw':
                    raw=row.get('payload')
                    if raw is None:raw=json.loads(base64.b64decode(row['raw_base64'],validate=True))
                    if not isinstance(raw,dict) or not raw.get('msg_id'):raise ValueError('invalid raw claim requires manual correction in a new input file')
                    asyncio.run(publish(a.broker,a.topic,raw))
                    entry={'line':number,'success':True,'msg_id':raw['msg_id']}
                else:
                    if not isinstance(row.get('flagged'),bool):raise ValueError('flagged must be a boolean')
                    body={k:row[k] for k in ('msg_id','confidence','model_version','content','source_platform')}
                    body.update(label='1' if row['flagged'] else '0',published_at=row.get('ingest_timestamp',''),inference_timestamp=row.get('inference_timestamp',''))
                    response=http(a.api+'/api/reports',body,key);entry={'line':number,'success':True,'report_id':response['id']}
            except Exception as exc:failed+=1;entry={'line':number,'success':False,'error':str(exc)[:500]}
            entry['timestamp']=dt.datetime.now(dt.timezone.utc).isoformat();result.write(json.dumps(entry)+'\n');result.flush();os.fsync(result.fileno())
    return int(failed>0)


async def publish(broker,topic,payload):
    from aiokafka import AIOKafkaProducer
    producer=AIOKafkaProducer(bootstrap_servers=broker,enable_idempotence=True)
    await producer.start()
    try:await producer.send_and_wait(topic,json.dumps(payload).encode())
    finally:await producer.stop()


if __name__=='__main__':raise SystemExit(main())
