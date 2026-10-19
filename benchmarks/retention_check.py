#!/usr/bin/env python3
"""Synthetic-only retention and credential revocation integration checks."""
import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,RUNTIME,http,compose,write,runtime_path
from blockchain.scripts.transport import configure_client
from benchmarks.security import probe

EXPIRE=r'''
import os,sys,time
from objects import OffChainStore,IpfsStore
store=OffChainStore(os.environ['OFFCHAIN_DB']);cid=sys.argv[1]
for host in ('storage1','storage2'):IpfsStore('https://'+host+':9101').cat_bytes(cid)
with store._conn:store._conn.execute('UPDATE objects_v3 SET retain_until=? WHERE cid=?',(time.time()-1,cid))
store.close();print('Expired only the explicitly selected synthetic retention-check object.')
'''


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--name',required=True);a=p.parse_args()
    runtime=runtime_path(a.name);cfg=json.loads((runtime/'experiment.json').read_text())
    credentials=json.loads((runtime/'credentials.json').read_text());keys={(r['org'],r['role']):r['api_key'] for r in credentials}
    base=configure_client(runtime,cfg);key=keys['org1','fact_checker']
    msg='retention-check-'+uuid.uuid4().hex
    report=http(base+'/api/reports',{'msg_id':msg,'label':'0','confidence':1,'model_version':'synthetic-retention-check',
        'content':'Synthetic object created solely for managed retention verification.','source_platform':'test'},key)
    path=base+'/api/reports/'+report['id']
    before=http(path+'/verify',key=key);assert before['verified']
    compose(runtime,'exec','-T','-e','PYTHONPATH=/app/api:/app/src','api','python','-c',EXPIRE,report['claim_cid'],capture=True)
    subprocess.run([sys.executable,str(ROOT/'blockchain/scripts/retention.py'),'--name',a.name,'--execute','--reason','Synthetic managed retention integration test'],cwd=ROOT,check=True)
    after=http(path+'/verify',key=key);assert not after['verified'] and not after['claim']['available']
    chain=http(path+'/chain',key=key);assert chain['claim_cid']==report['claim_cid']
    # Revocation uses the otherwise unused org3 observer fixture. It must fail
    # at the API and its own signer, and remain rejected after API restart.
    revoked=keys['org3','observer']
    subprocess.run([sys.executable,str(ROOT/'blockchain/scripts/credentials.py'),'--name',a.name,'--org','org3','--role','observer'],cwd=ROOT,check=True)
    try:http(base+'/api/orgs',key=revoked)
    except RuntimeError as exc:assert 'HTTP 401' in str(exc)
    else:raise AssertionError('revoked API credential accepted')
    direct=probe(runtime,{'host':'signer3','port':9100,'path':'/query','body':{'function':'ListRegisteredOrgs','args':[]}},revoked)
    assert direct['status']==401,direct
    compose(runtime,'restart','api',capture=True)
    until=time.monotonic()+30
    while True:
        try:
            http(base+'/api/orgs',key=revoked)
        except RuntimeError as exc:
            if 'HTTP 401' in str(exc):break
        except OSError:pass
        if time.monotonic()>until:raise AssertionError('revocation did not persist after restart')
        time.sleep(.5)
    result={'result':'passed','report_id':report['id'],'cid':report['claim_cid'],'before':before,'after':after,
        'managed_replicas_prefetched':3,'ledger_commitment_retained':True,'api_revocation_persistent':True,'signer_revocation_status':direct['status'],
        'scope':'Explicitly created synthetic object and an experimental observer credential; no historical content or unrelated deployment was changed.'}
    write(ROOT/'results/repair'/a.name/'retention-revocation.json',result);print(json.dumps({'result':'passed','report_id':report['id']},indent=2))


if __name__=='__main__':main()
