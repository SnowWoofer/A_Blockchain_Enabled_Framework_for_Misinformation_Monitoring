"""Complete missing repeats after the campaign and exercise managed recovery."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,write


def main():
    out=ROOT/'results/repair';records=[]
    print('Waiting for the main campaign before any follow-up workload.',flush=True)
    while True:
        try:campaign=json.loads((out/'campaign-status.json').read_text())
        except (FileNotFoundError,ValueError):campaign=[]
        done=campaign and campaign[-1].get('status')!='running' and (
            campaign[-1]['task']=='real-kafka-pipeline' or campaign[-1]['task']=='demonstration-up' and campaign[-1]['status']=='failed')
        if done:break
        time.sleep(5)
    def task(name,args,timeout=3600):
        log=out/'campaign-logs'/f'followup-{name}.log';row={'task':name,'status':'running','command':args,
            'started_at':dt.datetime.now(dt.timezone.utc).isoformat()};records.append(row);write(out/'followup-status.json',records)
        print('Starting follow-up '+name,flush=True)
        try:
            with log.open('x') as handle:result=subprocess.run(args,cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT,timeout=timeout)
            row.update(status='completed' if result.returncode==0 else 'failed',returncode=result.returncode)
        except Exception as exc:row.update(status='failed',error=str(exc)[:500])
        row['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat();write(out/'followup-status.json',records)
        print(json.dumps({k:v for k,v in row.items() if k!='command'}),flush=True)
        return row['status']=='completed'
    # All performance work follows the model/pipeline run, with its child
    # processes stopped. Stop the demonstration stack during remaining repeats.
    task('pause-demo',[sys.executable,'blockchain/scripts/deployment.py','down','--name','demonstration'])
    for db in ('leveldb','couchdb'):
        count=0
        for path in out.glob('*/runs/write-100tps-*.json'):
            if not path.parent.parent.name.startswith(('dbverified-','dbcomplete-','dbfinal-')):continue
            d=json.loads(path.read_text())
            if d['experiment']['database']==db:count+=1
        if count<3:task('database-'+db,[sys.executable,'benchmarks/matrix.py','--suite','database','--databases',db,
            '--prefix','dbfinal-'+db,'--duration','60','--repeats',str(3-count)])
    scans=[]
    for path in out.glob('*/runs/scan-50tps-*.json'):
        d=json.loads(path.read_text())
        if d.get('experiment',{}).get('database')=='couchdb' and 'generator_policy' in d['summary']:scans.append(path)
    if len(scans)<3:task('scan',[sys.executable,'benchmarks/matrix.py','--suite','database','--databases','couchdb',
        '--operations','scan','--prefix','scanfinal','--duration','60','--repeats',str(3-len(scans))])
    for n in (5,10,15,20):
        paths=list(out.glob(f'scaleverified-lev-n{n}-*/runs/write-25tps-*.json'))
        if len(paths)<3:task('scale-'+str(n),[sys.executable,'benchmarks/matrix.py','--suite','scaling','--org-counts',str(n),
            '--prefix','scalefinal','--duration','60','--repeats',str(3-len(paths))])
    ready=task('restore-demo',[sys.executable,'blockchain/scripts/deployment.py','up','--name','demonstration'])
    if ready:
        task('retention-revocation',[sys.executable,'benchmarks/retention_check.py','--name','demonstration'],timeout=600)
    cpu=str(ROOT/'repair/.venv/bin/python')
    task('edits',[cpu,'repair/build_edits.py']);task('poster',[cpu,'repair/build_poster.py'])
    return int(any(r['status']!='completed' for r in records))
