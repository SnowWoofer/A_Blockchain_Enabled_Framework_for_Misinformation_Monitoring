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
from blockchain.scripts.transport import configure_client


from benchmarks.review import review_to_majority


def smoke(name):
    runtime=runtime_path(name);cfg=json.loads((runtime/'experiment.json').read_text())
    credentials=json.loads((runtime/'credentials.json').read_text())
    keys={(r['org'],r['role']):r['api_key'] for r in credentials}
    base=configure_client(runtime,cfg)
    key=lambda org='org1',role='fact_checker':keys[org,role]
    id_=uuid.uuid4().hex
    payload={'msg_id':'smoke-'+id_,'label':'1','confidence':.9,'model_version':'synthetic-smoke-v3',
        'content':'Synthetic integration claim; no real election assertion.','source_platform':'test'}
    timeline={};start=time.perf_counter()
    report=http(base+'/api/reports',payload,key());timeline['submission_ms']=(time.perf_counter()-start)*1000
    report_id=report['id'];path=base+'/api/reports/'+report_id
    try:http(base+'/api/public/reports/'+report_id)
    except RuntimeError as exc:assert 'HTTP 404' in str(exc)
    else:raise AssertionError('non-final report was publicly visible')
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
    original=http(path+'/chain',key=key())
    note={'note_id':'notice','text':'Synthetic clarification/action outcome recorded for demonstration only.','support':[]}
    first=http(path+'/notes',note,key())
    assert http(path+'/notes',note,key())==first
    for target,body in [(base+'/api/reports',payload|{'msg_id':'official-'+id_}),
                        (path+'/fact-check',{'outcome':'1','reasoning':'forbidden'}),
                        (path+'/notes',note)]:
        try:http(target,body,key('org1','official'))
        except RuntimeError as exc:
            assert 'HTTP 403' in str(exc);rejected.append('official:'+target.rsplit('/',1)[-1])
        else:raise AssertionError('official content write succeeded')
    listed=http(path+'/notes',key=key('org2','observer'))
    assert len(listed)==1 and listed[0]['verified'] and listed[0]['organisation']=='Org1MSP'
    try:http(path+'/notes',note|{'note_id':'forbidden'},key('org2'))
    except RuntimeError as exc:
        assert 'HTTP 403' in str(exc);rejected.append('org2:note_on_other_org_report')
    else:raise AssertionError('other organisation appended a note')
    assert http(path+'/chain',key=key())==original
    assert http(path+'/verify',key=key())['verified']
    public=http(base+'/api/public/reports/'+report_id)
    assert public['status']=='FINAL' and public['final_label']=='1'
    assert set(public)=={'id','status','final_label','submitted_by','finalized_at','required_votes','content_hash','fact_checks'}
    assert all(set(v)=={'checker_msp','outcome','timestamp','txid','evidence_hash'} for v in public['fact_checks'])
    published=http(base+'/api/public/reports')
    assert any(row['id']==report_id for row in published['items'])
    try:http(path)
    except RuntimeError as exc:assert 'HTTP 401' in str(exc)
    else:raise AssertionError('protected report exposed without authentication')
    result={'configuration':cfg,'report_id':report_id,'note':first,
        'timeline':timeline,'rejected_operations':rejected,'verification':verification,
        'history':http(path+'/history',key=key()),'public_verdict':public,'result':'passed',
        'note':'Scripted reviewer operations; not human review time, AI accuracy or perceived trust.'}
    out=ROOT/'results/repair'/name
    write(out/('smoke-'+id_+'.json'),result)
    print(json.dumps({'result':'passed','report_id':report_id,'timeline':timeline},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--name',required=True);smoke(p.parse_args().name)
