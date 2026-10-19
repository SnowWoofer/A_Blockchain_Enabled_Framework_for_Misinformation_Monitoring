#!/usr/bin/env python3
"""Dry-run or execute retention on an isolated deployment's managed replicas.

Deletion cannot remove third-party copies or on-chain commitments. Execution
requires a reason and affects only expired local objects and managed IPFS nodes.
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from blockchain.scripts.deployment import RUNTIME,compose,runtime_path

SCRIPT=r'''
import datetime,json,os,sys,time,urllib.request,urllib.error
from objects import OffChainStore
options=json.loads(sys.argv[1]);store=OffChainStore(os.environ['OFFCHAIN_DB'])
rows=store.retention_candidates();events=[]
def request(host,path):
    req=urllib.request.Request('https://'+host+':9101'+path,data=b'',method='POST',headers={'X-Service-Key':os.environ['IPFS_SERVICE_KEY']})
    with urllib.request.urlopen(req,timeout=65) as r:return json.load(r)
for row in rows:
    event={'cid':row['cid'],'kind':row['kind'],'retain_until':row['retain_until'],'executed':False}
    if options['execute']:
        try:
            for host in ('storage','storage1','storage2'):
                request(host,'/admin/unpin/'+row['cid'])
            for host in ('storage','storage1','storage2'):request(host,'/admin/gc')
            store.forget_local(row['cid'],options['reason']);event['executed']=True
        except Exception as exc:event['error']=type(exc).__name__
    events.append(event)
result={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'reason':options['reason'],'events':events,
    'limitation':'Managed copies only; ledger commitments remain and external copies cannot be recalled.'}
if options['execute']:
    with open('/data/retention-events.jsonl','a') as handle:
        handle.write(json.dumps(result)+'\n');handle.flush();os.fsync(handle.fileno())
store.close();print(json.dumps(result))
if any('error' in e for e in events):sys.exit(1)
'''


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--name',default='development')
    p.add_argument('--execute',action='store_true');p.add_argument('--reason',default='')
    a=p.parse_args()
    if a.execute and not a.reason.strip():p.error('--execute requires --reason')
    compose(runtime_path(a.name),'exec','-T','-e','PYTHONPATH=/app/api:/app/src','api','python','-c',SCRIPT,json.dumps(vars(a)))


if __name__=='__main__':main()
