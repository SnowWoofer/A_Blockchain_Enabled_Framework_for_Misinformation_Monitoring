#!/usr/bin/env python3
"""Check live TLS trust/hostname rejection and confinement of local HTTP RPC."""
import argparse
import json
from pathlib import Path
import socket
import ssl
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,runtime_path,compose,write
from blockchain.scripts.transport import configure_client,kafka_options
from blockchain.scripts.run_support import RunLog

INTERNAL=r'''
const tls=require('tls'),net=require('net'),fs=require('fs');
const spec=JSON.parse(process.argv[1]);
function encrypted(host,port,servername,ca){return new Promise(resolve=>{
 const c=tls.connect({host,port,servername,ca,minVersion:'TLSv1.2'},()=>{c.end();resolve({accepted:true,protocol:c.getProtocol()})});
 c.setTimeout(5000,()=>c.destroy(new Error('timeout')));c.on('error',e=>resolve({accepted:false,error:e.code||e.message}));
})}
function unreachable(host,port){return new Promise(resolve=>{
 const c=net.connect({host,port},()=>{c.end();resolve(false)});
 c.setTimeout(3000,()=>c.destroy(new Error('timeout')));c.on('error',e=>resolve(e.code==='ECONNREFUSED'));
})}
(async()=>{
 const ca=fs.readFileSync('/transport/ca.crt'),rows=[];
 for(const [host,port] of spec.endpoints){
  const good=await encrypted(host,port,host,ca),bad=await encrypted(host,port,'wrong.invalid',ca),unknown=await encrypted(host,port,host,[]);
  rows.push({host,port,good,bad,unknown,passed:good.accepted&&!bad.accepted&&!unknown.accepted});
 }
 for(const [host,port] of spec.local){const closed=await unreachable(host,port);rows.push({host,port,loopback_rpc_not_exposed:closed,passed:closed})}
 console.log(JSON.stringify(rows));
})().catch(()=>process.exit(1));
'''


def handshake(port,context,hostname='localhost'):
    with socket.create_connection(('127.0.0.1',port),timeout=5) as raw:
        with context.wrap_socket(raw,server_hostname=hostname) as connection:
            return connection.version()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--name',default='development');a=p.parse_args()
    runtime=runtime_path(a.name);cfg=json.loads((runtime/'experiment.json').read_text())
    if cfg['backend']!='fabric':p.error('this integration check requires a Fabric deployment')
    base=configure_client(runtime,cfg);rows=[]
    with RunLog(ROOT,a.name,'transport') as log:
        trusted=ssl.create_default_context(cafile=str(runtime/'transport/ca.crt'))
        with urllib.request.urlopen(base+'/health',context=trusted,timeout=10) as response:
            assert json.load(response)['status']=='ok'
        rows.append({'operation':'api_https_verified','passed':True})
        for description,context,hostname in [('api_unknown_ca',ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),'localhost'),
                ('api_wrong_hostname',trusted,'wrong.invalid')]:
            try:handshake(cfg['port_base'],context,hostname)
            except ssl.SSLCertVerificationError:rows.append({'operation':description,'passed':True})
            else:rows.append({'operation':description,'passed':False})
        try:
            with urllib.request.urlopen(base.replace('https:','http:')+'/health',timeout=5) as response:
                accepted=200<=response.status<300
        except (OSError,ValueError):accepted=False
        rows.append({'operation':'api_plaintext_rejected','passed':not accepted})
        endpoints=[['api',8000],['chaincode',9999]] if cfg['backend']=='fabric' else [['api',8000]]
        endpoints.extend([[f'signer{i}',9100] for i in range(1,cfg['orgs']+1)] if cfg['backend']=='fabric' else [])
        endpoints.extend([[host,9101] for host in ['storage','storage1','storage2']])
        local=[[f'ipfs{i}',5001] for i in range(3)]
        if cfg['database']=='couchdb':local.extend([[f'peer{i}',5984] for i in range(1,cfg['orgs']+1)])
        result=compose(runtime,'exec','-T','signer1','node','-e',INTERNAL,json.dumps({'endpoints':endpoints,'local':local}),capture=True,timeout=120)
        rows.extend(json.loads(result.stdout.strip().splitlines()[-1]))
        definition=json.loads((runtime/'compose.json').read_text())
        if 'kafka' in definition['services']:
            port=cfg['port_base']+4
            rows.append({'operation':'kafka_mtls_verified','protocol':handshake(port,kafka_options(runtime)['ssl_context']),'passed':True})
            # TLS 1.2 makes missing-certificate rejection occur at handshake,
            # rather than a later TLS 1.3 post-handshake read.
            no_identity=ssl.create_default_context(cafile=str(runtime/'transport/ca.crt'))
            no_identity.maximum_version=ssl.TLSVersion.TLSv1_2
            try:handshake(port,no_identity)
            except ssl.SSLError:rejected=True
            else:rejected=False
            rows.append({'operation':'kafka_client_without_certificate_rejected','passed':rejected})
        passed=all(r['passed'] for r in rows)
        write(log.folder/'result.json',{'result':'passed' if passed else 'failed','configuration':cfg,'checks':rows,
            'scope':'Core TLS trust and hostname rejection; Kafka mTLS if started; Kubo/CouchDB HTTP loopback confinement. Optional Explorer UI is loopback HTTP.'})
        log.say(f'Transport {"PASSED" if passed else "FAILED"}: {len(rows)} checks; {log.folder.relative_to(ROOT)}/result.json')
        return int(not passed)


if __name__=='__main__':raise SystemExit(main())
