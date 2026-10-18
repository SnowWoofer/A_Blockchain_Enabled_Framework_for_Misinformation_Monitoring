#!/usr/bin/env python3
"""Exercise the public API against a provisioned isolated deployment."""
from pathlib import Path
import argparse
import json
import time
import uuid
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,RUNTIME,http,write,runtime_path


from benchmarks.review import review_to_majority


def smoke(name):
    runtime=runtime_path(name);cfg=json.loads((runtime/'experiment.json').read_text())
    credentials=json.loads((runtime/'credentials.json').read_text())
    keys={(r['org'],r['role']):r['api_key'] for r in credentials}
    base=f'http://127.0.0.1:{cfg["port_base"]}'
    key=lambda org='org1',role='fact_checker':keys[org,role]
    id_=uuid.uuid4().hex
    payload={'msg_id':'smoke-'+id_,'label':'1','confidence':.9,'model_version':'synthetic-smoke-v3',
        'content':'Synthetic integration claim; no real election assertion.','source_platform':'test'}
    timeline={};start=time.perf_counter()
    report=http(base+'/api/reports',payload,key());timeline['submission_ms']=(time.perf_counter()-start)*1000
    report_id=report['id'];path=base+'/api/reports/'+report_id
    before=time.perf_counter()
    reviewers={org:value for (org,role),value in keys.items() if role=='fact_checker'}
    review_to_majority(path,http(path+'/chain',key=key()),reviewers,'1','Synthetic independent assessment')
    timeline['review_and_finalisation_ms']=(time.perf_counter()-before)*1000
    assert http(path+'/chain',key=key())['final_label']=='1'
    verification=http(path+'/verify',key=key());assert verification['verified'],verification
    timeline['complete_workflow_ms']=(time.perf_counter()-start)*1000
    # Retry and rejection probes are outside the measured workflow trace.
    assert http(base+'/api/reports',payload,key())['id']==report_id
    rejected=[]
    for org,role in [('org1','fact_checker'),('org2','observer')]:
        try:http(path+'/fact-check',{'outcome':'1','reasoning':'test','support':[]},key(org,role))
        except RuntimeError:rejected.append(org+':'+role)
        else:raise AssertionError('unauthorised/self vote succeeded')
    correction=http(path+'/corrections',payload|{'msg_id':'correction-'+id_,'content':'Synthetic corrected claim.','label':'0'},key())
    newer=base+'/api/reports/'+correction['id']
    review_to_majority(newer,http(newer+'/chain',key=key()),reviewers,'0','Synthetic correction assessment')
    old=http(path+'/chain',key=key());assert old['status']=='SUPERSEDED' and old['final_label']=='1'
    http(newer+'/interventions',{'intervention_id':'notice','action':'Publish synthetic correction','outcome':'Recorded for demonstration','support':[]},key('org2'))
    assert http(newer+'/verify',key=key())['verified']
    result={'configuration':cfg,'report_id':report_id,'correction_id':correction['id'],
        'timeline':timeline,'rejected_operations':rejected,'verification':verification,
        'history':http(path+'/history',key=key()),'result':'passed',
        'note':'Scripted reviewer operations; not human review time, AI accuracy or perceived trust.'}
    out=ROOT/'results/repair'/name
    write(out/('smoke-'+id_+'.json'),result)
    print(json.dumps({'result':'passed','report_id':report_id,'timeline':timeline},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--name',required=True);smoke(p.parse_args().name)
