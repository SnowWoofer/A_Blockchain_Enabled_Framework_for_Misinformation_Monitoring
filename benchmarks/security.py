#!/usr/bin/env python3
"""Probe actual signer and storage boundaries without printing credentials."""
import argparse
import hashlib
import json
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,RUNTIME,compose,write,runtime_path

SCRIPT=r'''
const http=require('http');const x=JSON.parse(process.argv[1]);
const req=http.request({host:x.host,port:x.port,path:x.path,method:x.body?'POST':'GET',headers:{'Content-Type':'application/json',[x.header||'X-API-Key']:process.env.PROBE_KEY||''}},res=>{
 let text='';res.on('data',d=>text+=d);res.on('end',()=>{let body;try{body=JSON.parse(text)}catch(_){body=text.slice(0,300)}console.log(JSON.stringify({status:res.statusCode,body}))})});
req.on('error',()=>process.exit(2));req.end(x.body?JSON.stringify(x.body):undefined);
'''


def probe(runtime,spec,key=''):
    result=compose(runtime,'exec','-T','-e','PROBE_KEY','signer1','node','-e',SCRIPT,json.dumps(spec),capture=True,env={'PROBE_KEY':key})
    return json.loads(result.stdout.strip().splitlines()[-1])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--name',required=True);p.add_argument('--diagnose',action='store_true')
    a=p.parse_args();runtime=runtime_path(a.name);credentials=json.loads((runtime/'credentials.json').read_text())
    results=[]
    for row in credentials:
        if a.diagnose and row['org']!='org1':continue
        registry=json.loads((runtime/('signer'+row['org'][3:]+'.json')).read_text())
        digest=hashlib.sha256(row['api_key'].encode()).hexdigest()
        active=any(r['digest']==digest and not r.get('revoked') for r in registry)
        result=probe(runtime,{'host':'signer'+row['org'][3:],'port':9100,'path':'/query','body':{'function':'ListRegisteredOrgs','args':[]}},row['api_key'])
        results.append({'operation':'authenticated_query' if active else 'revoked_query','org':row['org'],'role':row['role'],'expected':200 if active else 401,**result})
    if not a.diagnose:
        key=next(r['api_key'] for r in credentials if r['org']=='org1' and r['role']=='observer')
        other=next(r['api_key'] for r in credentials if r['org']=='org2' and r['role']=='official')
        official=next(r['api_key'] for r in credentials if r['org']=='org1' and r['role']=='official')
        for description,token,body,expected in [
            ('missing_credential','',{'function':'ListRegisteredOrgs'},401),
            ('wrong_organisation_credential',other,{'function':'ListRegisteredOrgs'},401),
            ('caller_org_selection',key,{'function':'ListRegisteredOrgs','org':'org2'},400),
            ('observer_write',key,{'function':'RegisterOrg','args':[]},403)]:
            result=probe(runtime,{'host':'signer1','port':9100,'path':'/invoke' if description=='observer_write' else '/query','body':body},token)
            results.append({'operation':description,'expected':expected,**result})
        result=probe(runtime,{'host':'signer1','port':9100,'path':'/invoke','body':{'function':'Submit','args':[
            'non-finite-security-probe','a'*64,'1','NaN','security-probe','2026-01-01T00:00:00Z']}},official)
        results.append({'operation':'non_finite_wire_input','expected':400,**result})
        result=probe(runtime,{'host':'signer1','port':9100,'path':'/query','body':{'function':'ListRegisteredOrgs','args':[]}},official)
        results.append({'operation':'contract_survives_invalid_input','expected':200,**result})
        for host in ('storage','storage1','storage2'):
            result=probe(runtime,{'host':host,'port':9101,'path':'/cat/invalid','header':'X-Service-Key'})
            results.append({'operation':'storage_read_without_credential','host':host,'expected':401,**result})
    passed=all(r['status']==r['expected'] for r in results)
    output={'result':'passed' if passed else 'failed','probes':results}
    write(ROOT/'results/repair'/a.name/('security-'+str(time.time_ns())+'.json'),output)
    print(json.dumps(output,indent=2))
    return int(not passed)


if __name__=='__main__':raise SystemExit(main())
