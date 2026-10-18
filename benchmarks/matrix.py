#!/usr/bin/env python3
"""Sequential controlled SDK benchmark matrix; preserves every run and log."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,RUNTIME,prepare,provision,compose,write,runtime_path


def memory_guard():
    info={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.split()[1].isdigit()}
    if info.get('MemAvailable',0)<750000:raise RuntimeError('resource-constrained: less than 750 MiB available')


def measured(runtime,operation,rate,duration,repeat,count=0):
    memory_guard()
    cfg=json.loads((runtime/'experiment.json').read_text())
    spec={'operation':operation,'rate':rate,'duration':duration,'count':count,'prefix':f'matrix-{time.time_ns()}','maxInflight':500}
    started=dt.datetime.now(dt.timezone.utc).isoformat()
    before=compose(runtime,'stats','--no-stream','--format','json',capture=True).stdout
    result=compose(runtime,'exec','-T','signer1','node','/benchmark/load.js',json.dumps(spec),capture=True)
    data=json.loads(result.stdout.strip().splitlines()[-1]);data['experiment']=cfg;data['started_at']=started
    after=compose(runtime,'stats','--no-stream','--format','json',capture=True).stdout
    data['resources']={'before':before,'after':after}
    out=ROOT/'results/repair'/cfg['name']/'runs'
    write(out/f'{operation}-{rate:g}tps-r{repeat}.json',data)
    print(json.dumps({'experiment':cfg['name'],'operation':operation,'rate':rate,'repeat':repeat,**data['summary']}),flush=True)
    return data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite',choices=['database','scaling'],required=True)
    p.add_argument('--prefix',required=True)
    p.add_argument('--duration',type=int,default=60)
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--databases',nargs='+',choices=['leveldb','couchdb'],default=['leveldb','couchdb'])
    p.add_argument('--org-counts',nargs='+',type=int,default=[5,10,15,20])
    p.add_argument('--operations',nargs='+',choices=['point','page','scan','write'],default=['point','page','scan','write'])
    a=p.parse_args();results=[]
    cases=[(db,3) for db in a.databases] if a.suite=='database' else [('leveldb',n) for n in a.org_counts]
    for db,n in cases:
        for rep in range(1,a.repeats+1):
            name=f'{a.prefix}-{db[:3]}-n{n}-r{rep}'
            runtime=runtime_path(name)
            record={'name':name,'database':db,'orgs':n,'repeat':rep,'status':'started'}
            target=ROOT/'results/repair'/f'{a.prefix}-manifest.json'
            try:
                memory_guard()
                opts=argparse.Namespace(name=name,orgs=n,database=db,backend='fabric',port_base=18100)
                prepare(opts)
                logs=ROOT/'results/repair'/name/'provision.log';logs.parent.mkdir(parents=True,exist_ok=True)
                # Keep tool output bounded while retaining complete diagnostics.
                with logs.open('w') as handle:
                    subprocess.run([sys.executable,str(ROOT/'blockchain/scripts/deployment.py'),'up','--name',name],cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT,check=True,timeout=900)
                seed=measured(runtime,'seed',100,10,rep,1000)
                if seed['summary']['succeeded']!=1000:raise RuntimeError('initial seed incomplete')
                if a.suite=='database':
                    for op in ('point','page','scan'):
                        if op in a.operations:measured(runtime,op,50,a.duration,rep)
                    if 'write' in a.operations:
                        for rate in (25,50,100):measured(runtime,'write',rate,a.duration,rep)
                else:measured(runtime,'write',25,a.duration,rep)
                record['status']='completed'
            except Exception as exc:
                record.update(status='failed_or_resource_constrained',error=str(exc)[:1000])
                print(json.dumps(record),flush=True)
                if (runtime/'compose.json').exists():
                    try:write(ROOT/'results/repair'/name/'failure-services.log',compose(runtime,'logs','--tail','100',capture=True).stdout)
                    except subprocess.CalledProcessError:pass
            finally:
                if (runtime/'compose.json').exists():
                    try:compose(runtime,'down',capture=True)
                    except subprocess.CalledProcessError:record['cleanup_failed']=True
                results.append(record);write(target,results)
    return int(any(r['status']!='completed' for r in results))


if __name__=='__main__':raise SystemExit(main())
