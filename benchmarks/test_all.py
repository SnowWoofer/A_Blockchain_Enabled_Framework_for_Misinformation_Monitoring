#!/usr/bin/env python3
"""Fresh-deployment functional acceptance checks; never reuse or reset old state."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import traceback
import urllib.request

from blockchain.scripts.deployment import ROOT,runtime_path,source_manifest,write
from blockchain.scripts.transport import configure_client
from blockchain.scripts.run_support import RunLog


def fresh_name():
    return 'fulltest-'+dt.datetime.now(dt.timezone.utc).strftime('%m%d-%H%M%S')+'-'+secrets.token_hex(3)


def available_ports(base):
    if not 1024<=base<=65455:return False
    sockets=[]
    try:
        for offset in (0,1,3,4,5,6,80):
            sock=socket.socket();sockets.append(sock)
            sock.bind(('127.0.0.1',base+offset))
        return True
    except OSError:return False
    finally:
        for sock in sockets:sock.close()


def choose_ports(requested=None):
    if requested is not None:
        if not available_ports(requested):raise ValueError('requested port block is unavailable')
        return requested
    # Reserve a distinct candidate block per invocation; startup still fails if
    # another process takes a port after this preflight check.
    for _ in range(100):
        base=20000+100*secrets.randbelow(350)
        if available_ports(base):return base
    raise RuntimeError('no free local port block found')


def require_unused_projects(names):
    # Missing runtime files do not prove old named volumes are absent.
    for name in names:
        label='label=com.docker.compose.project=misinfo-v3-'+name
        for args in (['ps','-aq'],['volume','ls','-q'],['network','ls','-q']):
            result=subprocess.run(['docker',*args,'--filter',label],check=True,
                capture_output=True,text=True,timeout=30)
            if result.stdout.strip():raise ValueError('existing Docker resources refused: '+name)


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--name',help='optional NEW name; existing names are always rejected')
    p.add_argument('--orgs',type=int,default=3)
    p.add_argument('--database',choices=['leveldb','couchdb'],default='leveldb')
    p.add_argument('--port-base',type=int,help='otherwise select an available local port block')
    p.add_argument('--skip-explorer',action='store_true',help='explicitly omit the dashboard integration checks')
    p.add_argument('--matrix',action='store_true',help='also check fresh 3-org CouchDB and 5-org LevelDB deployments')
    p.add_argument('--evaluate-model',action='store_true',help='also evaluate 90 supplied examples with FP32 and INT8')
    p.add_argument('--keep-running',action='store_true',help='leave the main deployment running after success')
    p.add_argument('--plan',action='store_true',help='show coverage without Docker access or creating deployments')
    p.add_argument('--verbose',action='store_true')
    a=p.parse_args(argv)
    if a.orgs<3:p.error('--orgs must be at least 3')
    return a


class AcceptanceRun:
    def __init__(self,a,name,port,log):
        self.a,self.name,self.port,self.log=a,name,port,log
        self.steps=[];self.created=[];self.explorers=set()
        self.python=sys.executable

    def command(self,label,args,timeout=300):
        self.log.step(label)
        started=time.monotonic()
        # Commands/headers containing credentials are never part of this list.
        process=subprocess.Popen([str(x) for x in args],cwd=ROOT,start_new_session=True)
        try:code=process.wait(timeout=timeout)
        except BaseException as exc:
            # Terminate this command's workers too, never unrelated processes.
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait(timeout=10)
            self.steps.append({'name':label,'passed':False,'error_type':type(exc).__name__,
                'elapsed_s':time.monotonic()-started})
            raise
        self.steps.append({'name':label,'passed':code==0,
            'returncode':code,'elapsed_s':time.monotonic()-started})
        if code:raise RuntimeError(f'{label} failed (exit {code})')

    def module(self,label,module,*args,timeout=300):
        self.command(label,[self.python,'-m',module,*args],timeout)

    def deploy(self,name,database,orgs):
        path=runtime_path(name)
        if path.exists():raise ValueError(f'deployment already exists: {name}; choose a fresh name')
        # Track ownership before startup so partial failures can be stopped too.
        self.created.append(name)
        self.module('Fresh '+name,'blockchain.scripts.deployment','up','--name',name,
            '--database',database,'--orgs',str(orgs),'--port-base',str(self.port),timeout=600)
        self.module('Workflow '+name,'benchmarks.smoke','--name',name,timeout=180)

    def stop(self,name):
        self.module('Stop '+name,'blockchain.scripts.deployment','down','--name',name,timeout=120)

    def persisted_reads(self,name,report_id):
        self.log.step('Verify retained verdict and evidence over trusted HTTPS')
        runtime=runtime_path(name);base=configure_client(runtime)
        # urllib's default opener may have cached system CAs during an earlier
        # HTTP-only Explorer request. Use this deployment's CA explicitly.
        context=ssl.create_default_context(cafile=str(runtime/'transport/ca.crt'))
        opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
        def get(path,key=None):
            request=urllib.request.Request(base+path,headers={'X-API-Key':key} if key else {})
            with opener.open(request,timeout=10) as response:return json.load(response)
        keys=json.loads((runtime/'credentials.json').read_text())
        key=next(r['api_key'] for r in keys if r['org']=='org1' and r['role']=='fact_checker')
        until=time.monotonic()+60
        while True:
            try:
                verdict=get('/api/public/reports/'+report_id)
                proof=get('/api/reports/'+report_id+'/verify',key=key)
                assert verdict['status']=='FINAL' and proof['verified'] and proof['history_verified']
                break
            except (OSError,RuntimeError):
                if time.monotonic()>until:raise
                time.sleep(1)
        self.steps.append({'name':'Existing verdict/evidence survive restart','passed':True})

    def explorer(self):
        from blockchain.explorer.manage import wait_ready
        self.explorers.add(self.name)
        self.module('Explorer indexing','blockchain.explorer.manage','up','--name',self.name,timeout=360)
        self.module('Explorer stop','blockchain.explorer.manage','down','--name',self.name,timeout=120)
        self.module('Explorer restart','blockchain.explorer.manage','up','--name',self.name,timeout=360)
        folder=runtime_path(self.name)/'explorer'
        # Reads private files locally; never log the login/password/token.
        state=json.loads((folder/'state.json').read_text())
        private=json.loads((folder/'secrets.json').read_text())
        ready=wait_ready(state,private)
        assert ready['authenticated']
        self.steps.append({'name':'Explorer authenticated channel/peer/chaincode reads after restart','passed':True})
        self.module('Explorer final stop','blockchain.explorer.manage','down','--name',self.name,timeout=120)
        self.explorers.remove(self.name)

    def run(self):
        self.command('Automated regression',[self.python,'-c',
            'from benchmarks.regression import main; raise SystemExit(main())'],timeout=600)
        shutil.copyfile(ROOT/'results/repair/validation.json',self.log.folder/'regression.json')
        self.deploy(self.name,self.a.database,self.a.orgs)
        self.module('Manual API routes and public/private boundaries','benchmarks.api_check','--name',self.name,timeout=300)
        self.module('Signer/storage/direct-contract permissions','benchmarks.security','--name',self.name,timeout=300)
        self.module('Balanced multilingual feed','benchmarks.pipeline','--name',self.name,'--samples','6','--seed','100',timeout=600)
        for repeat in (1,2):
            self.module(f'Repeated feed {repeat}','benchmarks.pipeline','--name',self.name,
                '--samples','10','--seed','100',timeout=600)
        self.module('Mixed submitters and scripted label overturns','benchmarks.pipeline','--name',self.name,
            '--samples','6','--seed','100','--random','50','--rejection-percent','50',timeout=600)
        self.module('TLS and Kafka mutual TLS','benchmarks.transport_check','--name',self.name,timeout=300)
        for count,workers in ((1,1),(5,2)):
            self.module(f'Caliper {count} writes then reads','benchmarks.caliper.run','--name',self.name,
                '--count',str(count),'--workers',str(workers),'--load',str(workers),timeout=600)
        if not self.a.skip_explorer:self.explorer()
        api_path=max((ROOT/'results/repair'/self.name).glob('api-check-*.json'),key=lambda p:p.stat().st_mtime)
        report_id=json.loads(api_path.read_text())['report_id']
        self.stop(self.name)
        self.module('Reopen same deployment without deleting data','blockchain.scripts.deployment',
            'up','--name',self.name,timeout=600)
        self.persisted_reads(self.name,report_id)
        # Retention changes only synthetic state in a separate disposable network.
        # Stop the main network first to avoid doubling memory demand.
        self.stop(self.name)
        retention_name=self.name+'-ret'
        self.deploy(retention_name,self.a.database,3)
        self.module('Managed retention and persistent credential revocation','benchmarks.retention_check',
            '--name',retention_name,timeout=300)
        self.stop(retention_name)
        if self.a.matrix:
            for suffix,database,orgs in (('-couch3','couchdb',3),('-level5','leveldb',5)):
                name=self.name+suffix
                self.deploy(name,database,orgs)
                self.module('Feed '+name,'benchmarks.pipeline','--name',name,'--samples','6','--seed','100',timeout=600)
                self.module('Caliper '+name,'benchmarks.caliper.run','--name',name,'--count','5','--workers','2','--load','2',timeout=600)
                self.stop(name)
        if self.a.evaluate_model:
            for quantization in ('none','int8'):
                self.module('Model evaluation '+quantization,'benchmarks.evaluate_model','--quantization',quantization,
                    '--samples','90','--seed','619','--batch-size','4','--threads','2',
                    '--output',str(self.log.folder/('model-'+quantization+'.json')),timeout=900)
        if self.a.keep_running:
            self.module('Leave main deployment running','blockchain.scripts.deployment','up','--name',self.name,timeout=600)
            self.persisted_reads(self.name,report_id)

    def cleanup(self,successful):
        errors=[]
        for name in sorted(self.explorers):
            try:self.module('Cleanup Explorer '+name,'blockchain.explorer.manage','down','--name',name,timeout=120)
            except Exception:errors.append('Explorer '+name)
        for name in reversed(self.created):
            if successful and self.a.keep_running and name==self.name:continue
            if not (runtime_path(name)/'compose.json').exists():continue
            try:self.stop(name)
            except Exception:errors.append(name)
        return errors


def preflight():
    for executable in ('docker','go','node'):
        if shutil.which(executable) is None:raise RuntimeError(executable+' is required')
    subprocess.run(['docker','info','--format','{{.ServerVersion}}'],check=True,timeout=30)
    model=ROOT/'apps/flagging-engine/model'
    for item in ('config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json'):
        if not (model/item).is_file():raise ValueError('missing model file: '+item)
    if not list(model.glob('*.safetensors')):raise ValueError('supplied model weights are required')
    if not (ROOT/'data/translated_masked_samples_test.json').is_file():raise ValueError('supplied test dataset is required')
    for item in ('cryptogen','configtxgen','osnadmin'):
        if not os.access(ROOT/'blockchain/fabric-samples/bin'/item,os.X_OK):raise ValueError('missing Fabric tool: '+item)


def main(argv=None):
    a=parse_args(argv)
    if a.plan:
        print('Fresh unique deployment -> regression -> workflow/API/permissions -> feed 6,10,10,mixed 6 -> TLS -> Caliper 1,5 -> Explorer/restart -> ledger persistence -> separate retention/revocation deployment')
        print('Optional: --matrix, --evaluate-model. No checkpoints or prior deployments are reused. Data is retained; own containers stop unless --keep-running succeeds.')
        return 0
    name=a.name or fresh_name()
    # Leave enough space for child deployment suffixes and never touch a saved name.
    runtime=runtime_path(name)
    if len(name)>32:raise ValueError('test name must be at most 32 characters')
    for candidate in (name,name+'-ret',name+'-couch3',name+'-level5'):
        if runtime_path(candidate).exists():raise ValueError('existing deployment refused: '+candidate)
    preflight()
    require_unused_projects((name,name+'-ret',name+'-couch3',name+'-level5'))
    port=choose_ports(a.port_base)
    with RunLog(ROOT,name,'test-all',a.verbose) as log:
        log.say(f'Deployment: {name}; port_base: {port}; previous runs are preserved')
        run=AcceptanceRun(a,name,port,log);error=None
        try:run.run()
        except (Exception,KeyboardInterrupt) as exc:
            error=exc
            log.say('FAILED: '+type(exc).__name__+'; inspect logs.txt for the failing stage')
            traceback.print_exc()
        cleanup_errors=run.cleanup(error is None)
        passed=error is None and not cleanup_errors
        result={'result':('passed_with_skips' if a.skip_explorer else 'passed') if passed else 'failed',
            'name':name,'port_base':port,'options':vars(a),'steps':run.steps,'cleanup_errors':cleanup_errors,
            'error_type':type(error).__name__ if error else None,'source_sha256':source_manifest(),
            'created_deployments':run.created,'skipped':['Explorer integration'] if a.skip_explorer else [],
            'optional_not_requested':[label for enabled,label in ((a.matrix,'additional database/org configurations'),
                (a.evaluate_model,'FP32/INT8 accuracy evaluation')) if not enabled],
            'data_retained':True,'main_left_running':bool(passed and a.keep_running),
            'scope':'Functional acceptance on fresh local deployments. Synthetic/scripted workflows; not exhaustive security, production readiness, physical-host resilience or a performance study.'}
        write(log.folder/'result.json',result)
        log.say('FULL TEST '+result['result'].upper()+': '+str((log.folder/'result.json').relative_to(ROOT)))
        return 0 if passed else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print('Full test preflight failed: '+str(exc),file=sys.stderr);raise SystemExit(1)
