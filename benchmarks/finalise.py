"""Verify the final demonstration, refresh checks and export research artefacts."""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT, write


def main():
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')
    records=[];target=ROOT/'results/repair'/('finalise-'+stamp+'.json')
    cpu=str(ROOT/'repair/.venv/bin/python')

    def step(name,args,timeout=900):
        path=ROOT/'results/repair/campaign-logs'/('finalise-'+stamp+'-'+name+'.log')
        row={'task':name,'status':'running','started_at':dt.datetime.now(dt.timezone.utc).isoformat(),
             'command':args,'log':str(path.relative_to(ROOT))}
        records.append(row);write(target,records);print('Starting '+name,flush=True)
        try:
            with path.open('x') as handle:
                result=subprocess.run(args,cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT,timeout=timeout)
            row.update(status='completed' if result.returncode==0 else 'failed',returncode=result.returncode)
        except Exception as exc:row.update(status='failed',error=str(exc)[:1000])
        row['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat();write(target,records)
        print(json.dumps({k:v for k,v in row.items() if k!='command'}),flush=True)
        return row['status']=='completed'

    try:
        if not step('restore-demo',[sys.executable,'blockchain/scripts/deployment.py','up','--name','demonstration']):return 1
        if not step('balanced-kafka',[cpu,'benchmarks/pipeline.py','--name','demonstration','--samples','6']):return 1
        if not step('regression',[sys.executable,'benchmarks/campaign.py','--phase','regression']):return 1
        for script in ('build_edits','build_figures','build_poster','check_deliverables'):
            if not step(script,[cpu,'repair/'+script+'.py']):return 1
        return 0
    finally:
        step('stop-demo',[sys.executable,'blockchain/scripts/deployment.py','down','--name','demonstration'])


if __name__=='__main__':raise SystemExit(main())
