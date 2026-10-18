#!/usr/bin/env python3
"""Matched scripted API workflows on fresh Fabric and central reference stacks."""
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,RUNTIME,compose,http,write,runtime_path

REWRITE=r'''
import hashlib,json,os,sqlite3,sys
from objects import OffChainStore
store=OffChainStore(os.environ['OFFCHAIN_DB']);db=sqlite3.connect(os.environ['REFERENCE_DB']);key='report:'+sys.argv[1]
record=json.loads(db.execute('SELECT v FROM state WHERE k=?',(key,)).fetchone()[0])
claim=store.get_object(record['claim_cid']);claim['source']['content']='Synthetic privileged-operator rewrite test'
cid,digest=store.put_object(claim,'claim');store.mark_committed(cid)
record.update(claim_cid=cid,content_hash=digest)
with db:
 db.execute('UPDATE state SET v=? WHERE k=?',(json.dumps(record),key))
 for seq,data in db.execute('SELECT seq,v FROM history WHERE k=?',(key,)).fetchall():
  previous=json.loads(data);previous.update(claim_cid=cid,content_hash=digest)
  db.execute('UPDATE history SET v=? WHERE seq=?',(json.dumps(previous),seq))
store.close();db.close();print(json.dumps({'privileged_rewrite_completed':True}))
'''


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prefix',default='comparison')
    p.add_argument('--samples',type=int,default=10);p.add_argument('--repeats',type=int,default=3)
    a=p.parse_args();manifest=[]
    for backend in ('fabric','sqlite-reference'):
        for rep in range(1,a.repeats+1):
            name=f'{a.prefix}-'+('fabric' if backend=='fabric' else 'central')+f'-r{rep}'
            runtime=runtime_path(name);out=ROOT/'results/repair'/name;out.mkdir(parents=True,exist_ok=True)
            record={'name':name,'backend':backend,'repeat':rep,'status':'started'}
            try:
                with (out/'provision.log').open('w') as log:
                    subprocess.run([sys.executable,str(ROOT/'blockchain/scripts/deployment.py'),'up','--name',name,'--backend',backend,'--port-base','18200'],
                        cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
                target=out/'workflow.csv'
                result=subprocess.run([sys.executable,str(ROOT/'benchmarks/workflow.py'),'--credentials',str(runtime/'credentials.json'),
                    '--blockchain-api','http://127.0.0.1:18200','--samples',str(a.samples),'--ai-pct','0','--seed','100','--output',str(target)],
                    cwd=ROOT,capture_output=True,text=True,timeout=900)
                write(out/'workflow.log',result.stdout+result.stderr)
                if result.returncode:raise RuntimeError('workflow failed; see retained log')
                write(out/'resources.json',compose(runtime,'stats','--no-stream','--format','json',capture=True).stdout)
                if backend=='sqlite-reference':
                    rows=list(csv.DictReader(target.open()));report_id=rows[0]['report_id']
                    compose(runtime,'exec','-T','-e','PYTHONPATH=/app/api:/app/src','api','python','-c',REWRITE,report_id,capture=True)
                    creds=json.loads((runtime/'credentials.json').read_text());key=next(r['api_key'] for r in creds if r['org']=='org1' and r['role']=='fact_checker')
                    verification=http('http://127.0.0.1:18200/api/reports/'+report_id+'/verify',key=key)
                    assert verification['verified']
                    write(out/'privileged-rewrite.json',{'expected':'internal verification accepts a coordinated privileged rewrite of central state/history and content commitment',
                        'observed_verified':verification['verified'],'report_id':report_id,
                        'boundary':'Central application/storage operator controls its own authority. Does not simulate a Fabric administrator or establish distributed institutional independence.'})
                record['status']='completed'
            except Exception as exc:
                record.update(status='failed',error=str(exc)[:1000])
            finally:
                if (runtime/'compose.json').exists():
                    try:compose(runtime,'down',capture=True)
                    except subprocess.CalledProcessError:record['cleanup_failed']=True
                manifest.append(record);write(ROOT/'results/repair'/f'{a.prefix}-manifest.json',manifest)
                print(json.dumps(record),flush=True)
    return int(any(r['status']!='completed' for r in manifest))


if __name__=='__main__':raise SystemExit(main())
