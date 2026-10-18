#!/usr/bin/env python3
"""Complete the authorised repair evaluation sequentially, retaining failures."""
import datetime as dt
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,write


def main():
    out=ROOT/'results/repair';status=[]
    manifest=out/'dbverified-manifest.json'
    print('Waiting for the active six-case database campaign to finish.',flush=True)
    while True:
        try:initial=json.loads(manifest.read_text())
        except (FileNotFoundError,ValueError):initial=[]
        if len(initial)>=6:break
        time.sleep(5)
    def task(name,args,timeout=7200):
        target=out/'campaign-logs'/f'{name}.log';target.parent.mkdir(parents=True,exist_ok=True)
        row={'task':name,'started_at':dt.datetime.now(dt.timezone.utc).isoformat(),'command':args,'status':'running'}
        status.append(row);write(out/'campaign-status.json',status);print('Starting '+name,flush=True)
        try:
            with target.open('x') as log:result=subprocess.run(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
            row.update(status='completed' if result.returncode==0 else 'failed',returncode=result.returncode)
        except Exception as exc:row.update(status='failed',error=str(exc)[:500])
        row['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat();write(out/'campaign-status.json',status)
        print(json.dumps({k:v for k,v in row.items() if k!='command'}),flush=True)
        return row['status']=='completed'
    for db in ('leveldb','couchdb'):
        completed=sum(r['database']==db and r['status']=='completed' for r in initial)
        missing=max(0,3-completed)
        if missing:task('database-fill-'+db,[sys.executable,'benchmarks/matrix.py','--suite','database','--databases',db,
            '--prefix','dbcomplete-'+db,'--duration','60','--repeats',str(missing)])
    scans=[]
    for path in out.glob('*/runs/scan-50tps-*.json'):
        data=json.loads(path.read_text())
        if data.get('experiment',{}).get('database')=='couchdb' and 'generator_policy' in data['summary']:scans.append(path)
    missing=max(0,3-len(scans))
    if missing:task('scan-fill',[sys.executable,'benchmarks/matrix.py','--suite','database','--databases','couchdb',
        '--operations','scan','--prefix','scancomplete','--duration','60','--repeats',str(missing)])
    task('scaling',[sys.executable,'benchmarks/matrix.py','--suite','scaling','--prefix','scaleverified','--duration','60','--repeats','3'])
    task('central-comparison',[sys.executable,'benchmarks/compare.py','--prefix','comparison','--samples','10','--repeats','3'])
    cpu=str(ROOT/'repair/.venv/bin/python')
    for quant in ('none','int8'):
        task('model-'+quant,[cpu,'benchmarks/evaluate_model.py','--quantization',quant,'--samples','90',
            '--output',f'results/repair/model-{quant}.json'],timeout=1800)
    ready=task('demonstration-up',[sys.executable,'blockchain/scripts/deployment.py','up','--name','demonstration','--port-base','18000'])
    if ready:
        task('demonstration-smoke',[sys.executable,'blockchain/scripts/deployment.py','smoke','--name','demonstration'])
        task('demonstration-security',[sys.executable,'benchmarks/security.py','--name','demonstration'])
        task('real-kafka-pipeline',[cpu,'benchmarks/pipeline.py','--name','demonstration','--samples','3'],timeout=900)
    return int(any(r['status']!='completed' for r in status))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['main','follow-up','regression','resume','performance','finalise'],default='main')
    args=parser.parse_args()
    if args.phase=='finalise':
        from benchmarks.finalise import main as finalise
        raise SystemExit(finalise())
    if args.phase in ('resume','performance'):
        from benchmarks.resume import main as resume
        raise SystemExit(resume(performance_only=args.phase=='performance'))
    if args.phase=='follow-up':
        from benchmarks.finish_checks import main as finish
        raise SystemExit(finish())
    if args.phase=='regression':
        from benchmarks.regression import main as regression
        raise SystemExit(regression())
    raise SystemExit(main())
