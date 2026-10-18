#!/usr/bin/env python3
"""Measure inference, submission and scripted review with explicit boundaries."""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import json
import hashlib
from pathlib import Path
import random
import time
import uuid
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import http,write


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',default='data/translated_masked_samples_test.json')
    p.add_argument('--credentials',required=True,help='private provisioned credential file')
    p.add_argument('--samples',type=int,default=10)
    p.add_argument('--ai-pct',type=int,default=100)
    p.add_argument('--endpoint',default='http://127.0.0.1:18005')
    p.add_argument('--blockchain-api',default='http://127.0.0.1:18000')
    p.add_argument('--seed',type=int,default=100)
    p.add_argument('--output',required=True)
    a=p.parse_args()
    if not 0<=a.ai_pct<=100 or a.samples<1:p.error('invalid samples or ai-pct')
    out=Path(a.output)
    if out.exists():p.error('output already exists; historical runs are immutable')
    data=json.loads(Path(a.data).read_text());selected=random.Random(a.seed).sample(data,min(a.samples,len(data)))
    creds=json.loads(Path(a.credentials).read_text());keys={r['org']:r['api_key'] for r in creds if r['role']=='fact_checker'}
    orgs=sorted(keys,key=lambda x:int(x[3:]));submitter=orgs[0]
    registered=http(a.blockchain_api+'/api/orgs',key=keys[submitter]);assert len(registered)>=3
    run_id=uuid.uuid4().hex;results=[]
    for i,sample in enumerate(selected):
        begin=time.perf_counter();row={'sample_id':sample.get('id',str(i)),'ground_truth':sample.get('classification'),
            'language':sample.get('language'),'run_id':run_id,'success':False,'inference_ms':0.0,
            'submission_ms':0.0,'review_finalisation_ms':0.0,'retry_wait_ms':0.0,'vote_requests':0}
        try:
            text=sample['statement'];truth='1' if sample['classification']=='misinformation' else '0'
            if i<round(len(selected)*a.ai_pct/100):
                t=time.perf_counter();prediction=http(a.endpoint+'/predict',{'post_text':text})
                row['inference_ms']=(time.perf_counter()-t)*1000
                label='1' if prediction['flagged'] else '0';confidence=prediction['confidence'];version=prediction['model_version'];row['prediction_source']='model'
            else:
                label=truth;confidence=1.0;version='manual-synthetic-label';row['prediction_source']='synthetic_label'
            row['proposed_label']=label
            body={'msg_id':run_id+'-'+str(i),'label':label,'confidence':confidence,'model_version':version,'content':text,'source_platform':'controlled-test'}
            t=time.perf_counter()
            for attempt in range(3):
                try:report=http(a.blockchain_api+'/api/reports',body,keys[submitter]);break
                except (RuntimeError,OSError):
                    if attempt==2:raise
                    wait=.5*2**attempt;time.sleep(wait);row['retry_wait_ms']+=wait*1000
            row['submission_ms']=(time.perf_counter()-t)*1000;row['report_id']=report['id']
            t=time.perf_counter();record=http(a.blockchain_api+'/api/reports/'+report['id']+'/chain',key=keys[submitter])
            eligible=set(record['eligible_reviewers'])
            for org in orgs:
                if f'Org{org[3:]}MSP' not in eligible:continue
                row['vote_requests']+=1
                http(a.blockchain_api+'/api/reports/'+report['id']+'/fact-check',
                    {'outcome':truth,'reasoning':'Scripted workload vote from synthetic source label; not human fact checking.','support':[]},keys[org])
                record=http(a.blockchain_api+'/api/reports/'+report['id']+'/chain',key=keys[org])
                if record['status'] in ('FINAL','DISPUTED','EXPIRED'):break
            row['review_finalisation_ms']=(time.perf_counter()-t)*1000
            row['final_status']=record['status'];row['final_label']=record.get('final_label','');row['success']=record['status']=='FINAL'
        except Exception as exc:
            row['error']=str(exc)[:500]
        row['workflow_ms']=(time.perf_counter()-begin)*1000
        results.append(row);print(json.dumps({k:v for k,v in row.items() if k not in ('error',)},ensure_ascii=False),flush=True)
    manifest={'schema_version':3,'run_id':run_id,'timestamp':dt.datetime.now(dt.timezone.utc).isoformat(),
        'seed':a.seed,'samples':len(results),'ai_pct':a.ai_pct,'source_data':a.data,
        'source_data_sha256':hashlib.sha256(Path(a.data).read_bytes()).hexdigest(),
        'timing_boundary':'before inference through authoritative final-status response, including retries; simulated reviewer time only',
        'note':'Reviewer votes use synthetic source labels. Verdict agreement is not measured human accuracy or public trust.'}
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',newline='') as handle:
        fields=sorted(set().union(*(r.keys() for r in results)));writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(results)
    write(out.with_suffix(out.suffix+'.manifest.json'),manifest)
    return 0 if all(r['success'] for r in results) else 1


if __name__=='__main__':raise SystemExit(main())
