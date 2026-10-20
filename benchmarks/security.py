#!/usr/bin/env python3
"""Probe actual signer and storage boundaries without printing credentials."""
import argparse
import hashlib
import json
import time
import uuid
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,RUNTIME,compose,write,runtime_path,http
from blockchain.scripts.transport import configure_client

SCRIPT=r'''
const http=require('https');const x=JSON.parse(process.argv[1]);
const req=http.request({host:x.host,port:x.port,path:x.path,method:x.body?'POST':'GET',headers:{'Content-Type':'application/json',[x.header||'X-API-Key']:process.env.PROBE_KEY||''}},res=>{
 let text='';res.on('data',d=>text+=d);res.on('end',()=>{let body;try{body=JSON.parse(text)}catch(_){body=text.slice(0,300)}console.log(JSON.stringify({status:res.statusCode,body}))})});
req.on('error',()=>process.exit(2));req.end(x.body?JSON.stringify(x.body):undefined);
'''

# Bypass the HTTP signer allowlist and exercise the contract with an actual
# official certificate. Evaluation simulates writes without committing them.
CONTRACT_ROLE_PROBE=r'''
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const grpc=require('@grpc/grpc-js');
const {connect,signers}=require('@hyperledger/fabric-gateway');
const num=process.env.ORG.slice(3),root=`/crypto/users/official${num}@org${num}.example.com/msp`;
const one=d=>fs.readFileSync(path.join(d,fs.readdirSync(d).find(n=>!n.startsWith('.'))));
const client=new grpc.Client(process.env.GATEWAY_TARGET,grpc.credentials.createSsl(fs.readFileSync('/peer-tls-ca.crt')),
 {'grpc.ssl_target_name_override':process.env.GATEWAY_PEER});
const gateway=connect({client,identity:{mspId:`Org${num}MSP`,credentials:one(root+'/signcerts')},
 signer:signers.newPrivateKeySigner(crypto.createPrivateKey(one(root+'/keystore'))),
 evaluateOptions:()=>({deadline:Date.now()+15000})});
(async()=>{
 const contract=gateway.getNetwork('mychannel').getContract('misinformation'),results=[];
 try {
  for(const [fn,args] of JSON.parse(process.argv[1])) {
   let outcome='unexpected_success';
   try {await contract.evaluateTransaction(fn,...args);}
   catch(e) {
    const message=[e.message,...(e.details||[]).map(d=>d.message)].join(' ');
    outcome=message.includes('required one of [fact_checker]')?'role_rejected':'unexpected_error';
   }
   results.push({function:fn,outcome});
  }
  console.log(JSON.stringify(results));
 } finally {gateway.close();client.close();}
})().catch(()=>{gateway.close();client.close();process.exitCode=1;});
'''


def probe(runtime,spec,key=''):
    result=compose(runtime,'exec','-T','-e','PROBE_KEY','signer1','node','-e',SCRIPT,json.dumps(spec),capture=True,env={'PROBE_KEY':key})
    return json.loads(result.stdout.strip().splitlines()[-1])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--name',required=True);p.add_argument('--diagnose',action='store_true')
    a=p.parse_args();runtime=runtime_path(a.name);credentials=json.loads((runtime/'credentials.json').read_text())
    cfg=json.loads((runtime/'experiment.json').read_text());configure_client(runtime,cfg)
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
        writer=next(r['api_key'] for r in credentials if r['org']=='org1' and r['role']=='fact_checker')
        result=probe(runtime,{'host':'signer1','port':9100,'path':'/invoke','body':{'function':'Submit','args':[
            'non-finite-security-probe','a'*64,'1','NaN','security-probe','2026-01-01T00:00:00Z']}},writer)
        results.append({'operation':'non_finite_wire_input','expected':400,**result})
        result=probe(runtime,{'host':'signer1','port':9100,'path':'/query','body':{'function':'ListRegisteredOrgs','args':[]}},official)
        results.append({'operation':'contract_survives_invalid_input','expected':200,**result})
        for host in ('storage','storage1','storage2'):
            result=probe(runtime,{'host':host,'port':9101,'path':'/cat/invalid','header':'X-Service-Key'})
            results.append({'operation':'storage_read_without_credential','host':host,'expected':401,**result})
        cfg=json.loads((runtime/'experiment.json').read_text())
        if cfg.get('workflow_version')=='governance-v1':
            base=configure_client(runtime,cfg)
            owner=next(r['api_key'] for r in credentials if r['org']=='org1' and r['role']=='fact_checker')
            report=http(base+'/api/reports',{'msg_id':'note-security-'+uuid.uuid4().hex,
                'label':'0','confidence':1,'model_version':'synthetic-security-probe',
                'content':'Synthetic note-ownership security fixture.','source_platform':'test'},owner)
            path=base+'/api/reports/'+report['id']
            note=http(path+'/notes',{'note_id':'ownership','text':'Synthetic ownership check.','support':[]},owner)
            args=[report['id'],'ownership',note['evidence_cid'],note['evidence_hash']]
            for org,role,expected in [('org1','observer',403),('org2','fact_checker',400),('org2','official',403),('org1','official',403)]:
                token=next(r['api_key'] for r in credentials if r['org']==org and r['role']==role)
                result=probe(runtime,{'host':'signer'+org[3:],'port':9100,'path':'/invoke',
                    'body':{'function':'RecordReportNote','args':args}},token)
                results.append({'operation':'direct_note_ownership','org':org,'role':role,'expected':expected,**result})
            for fn,args in [('Submit',['official-forbidden','a'*64,'1','.9','v','2026-01-01T00:00:00Z']),
                            ('SubmitReport',['{}']),('SubmitFactCheck',[report['id'],'1',note['evidence_cid'],note['evidence_hash']])]:
                result=probe(runtime,{'host':'signer1','port':9100,'path':'/invoke',
                    'body':{'function':fn,'args':args}},official)
                results.append({'operation':'official_content_write','function':fn,'expected':403,**result})
            direct=[['Submit',['official-direct','a'*64,'1','.9','v','2026-01-01T00:00:00Z']],
                    ['SubmitReport',[json.dumps({'id':'official-direct','content_hash':'a'*64,'claim_cid':note['evidence_cid'],
                        'label':'1','confidence':.9,'model_version':'v','timestamp':'2026-01-01T00:00:00Z'})]],
                    ['SubmitFactCheck',[report['id'],'1',note['evidence_cid'],note['evidence_hash']]],
                    ['RecordReportNote',[report['id'],'official-direct',note['evidence_cid'],note['evidence_hash']]]]
            response=compose(runtime,'exec','-T','signer1','node','-e',CONTRACT_ROLE_PROBE,json.dumps(direct),capture=True,timeout=90)
            for row in json.loads(response.stdout.strip().splitlines()[-1]):
                results.append({'operation':'direct_contract_official_write','function':row['function'],
                    'expected':'role_rejected','status':row['outcome']})
            for fn in ('FinalizeReport','RecordIntervention'):
                result=probe(runtime,{'host':'signer1','port':9100,'path':'/invoke',
                    'body':{'function':fn,'args':[report['id']]}},official)
                results.append({'operation':'retired_signer_operation','function':fn,'expected':403,**result})
            for suffix in ('finalize','expire','corrections','interventions'):
                result=probe(runtime,{'host':'api','port':8000,'path':'/api/reports/'+report['id']+'/'+suffix,
                    'body':{}},official)
                results.append({'operation':'removed_public_route','route':suffix,'expected':404,**result})
            proof=http(path+'/verify',key=owner)
            results.append({'operation':'note_integrity_after_permission_probes','expected':200,
                'status':200 if proof['verified'] else 409,'report_id':report['id']})
    passed=all(r['status']==r['expected'] for r in results)
    output={'result':'passed' if passed else 'failed','probes':results}
    write(ROOT/'results/repair'/a.name/('security-'+str(time.time_ns())+'.json'),output)
    print(json.dumps(output,indent=2))
    return int(not passed)


if __name__=='__main__':raise SystemExit(main())
