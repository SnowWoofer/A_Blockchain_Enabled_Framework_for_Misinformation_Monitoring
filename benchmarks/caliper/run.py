#!/usr/bin/env python3
"""Count-based official Caliper writes, followed by reads, on an existing deployment."""
import argparse
from html.parser import HTMLParser
import json
import math
import os
import re
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from blockchain.scripts.deployment import ROOT, runtime_path, compose, write, source_manifest
from blockchain.scripts.run_support import RunLog, benchmark_lock
from blockchain.scripts.transport import configure_client

IMAGE='misinfo-caliper:0.7.1-v3'


def network_config(cfg):
    return {'name':cfg['name'],'version':'2.0.0','caliper':{'blockchain':'fabric'},
        'info':{'StateDB':cfg['database'],'Organisations':cfg['orgs'],'Endorsement':cfg['endorsement']},
        'channels':[{'channelName':cfg['channel'],'contracts':[{'id':'misinformation'}]}],
        'organizations':[{'mspid':'Org1MSP','identities':{'certificates':[{'name':'official1',
            'clientPrivateKey':{'path':'/identity/keystore/key.pem'},'clientSignedCert':{'path':'/identity/signcerts/cert.pem'}}]},
            'peers':[{'endpoint':'peer1:7051','tlsCACerts':{'path':'/peer-tls.pem'},
                'grpcOptions':{'ssl-target-name-override':'peer0.org1.example.com'}}]}]}


def benchmark_config(a,run_id):
    rounds=[]
    for operation,module in [('write','submitReport.js'),('read','queryAllReports.js')]:
        rounds.append({'label':operation,'txNumber':a.count,
            'rateControl':{'type':'workload/count-fixed-load.js','opts':{'transactionLoad':a.load,'startTps':25}},
            'workload':{'module':'workload/'+module,'arguments':{'label':operation,'runId':run_id,
                'transactionCount':a.count,'workerCount':a.workers}}})
    return {'test':{'name':'counted-writes-then-reads','description':'Counted committed Submit write attempts, then full-scan read attempts; no REST/IPFS/AI.',
        'workers':{'number':a.workers},'rounds':rounds}}


class Tables(HTMLParser):
    def __init__(self):
        super().__init__();self.tables=[];self.table=None;self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='table':self.table=[]
        elif tag=='tr' and self.table is not None:self.row=[]
        elif tag in ('th','td') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('th','td') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()));self.cell=None
        elif tag=='tr' and self.row is not None:
            self.table.append(self.row);self.row=None
        elif tag=='table' and self.table is not None:
            self.tables.append(self.table);self.table=None


def summarize(folder,rounds):
    samples=[]
    for p in folder.glob('transactions-*.jsonl'):
        samples.extend(json.loads(line) for line in p.read_text().splitlines() if line.strip())
    rows=[]
    for round_ in rounds:
        selected=[r for r in samples if r['round']==round_['label']];good=[r for r in selected if r['success']]
        requested=round_['txNumber'];workers=round_['workload']['arguments']['workerCount']
        expected_workers={str(i):requested//workers+(i<requested%workers) for i in range(workers)}
        actual_workers={str(i):0 for i in range(workers)}
        for sample in selected:
            worker=str(sample['worker']);actual_workers[worker]=actual_workers.get(worker,0)+1
        span=(max(r['finished_ms'] for r in selected)-min(r['started_ms'] for r in selected))/1000 if selected else 0
        latencies=sorted(r['latency_ms'] for r in good)
        rows.append({'label':round_['label'],'operation':round_['label'].split('-')[0],
            'pending_load':round_['rateControl']['opts']['transactionLoad'], 'requested':requested,'attempted':len(selected),
            'expected_attempts_by_worker':expected_workers,'attempts_by_worker':actual_workers,
            'count_matches':len(selected)==requested and actual_workers==expected_workers,
            'succeeded':len(good),'failed':len(selected)-len(good), 'observed_elapsed_s':span,
            'successful_throughput_tps':len(good)/span if span else 0,
            'send_rate_tps':len(selected)/span if span else 0,
            'mean_latency_ms':statistics.mean(latencies) if latencies else None,
            'p95_latency_ms':latencies[max(0,math.ceil(.95*len(latencies))-1)] if latencies else None})
    parser=Tables();report=folder/'report.html'
    if report.exists():parser.feed(report.read_text())
    official=[]
    for table in parser.tables:
        if table and any('Throughput' in c for c in table[0]) and any(c in ('Succ','Success') for c in table[0]):
            official.extend(dict(zip(table[0],row)) for row in table[1:] if len(row)==len(table[0]))
    official=list({r['Name']:r for r in official}.values())
    for row in rows:
        actual=next((r for r in official if r['Name']==row['label']),None)
        if actual:
            if int(actual['Succ'])!=row['succeeded'] or int(actual['Fail'])!=row['failed']:
                raise RuntimeError('Caliper summary and transaction records disagree')
            row['workload_observed_throughput_tps']=row['successful_throughput_tps']
            row['caliper_reported_throughput_tps']=float(actual['Throughput (TPS)']) if actual['Throughput (TPS)']!='-' else 0
            # Caliper can count failed completions in its Throughput column.
            if row['succeeded'] and not row['failed']:row['successful_throughput_tps']=row['caliper_reported_throughput_tps']
            row['send_rate_tps']=float(actual['Send Rate (TPS)']) if actual['Send Rate (TPS)']!='-' else 0
            for field,column in [('mean_latency_ms','Avg Latency (s)'),('min_latency_ms','Min Latency (s)'),('max_latency_ms','Max Latency (s)')]:
                row[field]=float(actual[column])*1000 if actual[column]!='-' else None
    best={}
    for operation in ('read','write'):
        valid=[r for r in rows if r['operation']==operation and r['count_matches'] and not r['failed']]
        best[operation]=max(valid,key=lambda r:r['successful_throughput_tps']) if valid else None
    return {'rounds':rows,'highest_observed_error_free':best,'official_caliper_summary':official,
        'measurement':'caliper_reported_throughput_tps, send rate and mean/min/max latency come from the official HTML. Caliper throughput may include failed completions. successful_throughput_tps uses official throughput only for error-free rounds; otherwise it counts successful recorded completions over the workload interval including drain. p95 is workload-observed.',
        'limitation':'Observed throughput for the recorded count, workers and pending load, not a maximum-capacity search. Short runs include controller ramp-up. Per-transaction recording adds generator overhead.'}


def ledger_count(runtime):
    response=compose(runtime,'exec','-T','signer1','node','/benchmark/load.js',json.dumps({'operation':'count','rate':1}),capture=True,timeout=90)
    return json.loads(response.stdout.strip().splitlines()[-1])['records']


def run(a,runtime,cfg,log):
    run_id=uuid.uuid4().hex;name='caliper-'+run_id
    inspect=subprocess.run(['docker','image','inspect',IMAGE],capture_output=True,text=True)
    if a.rebuild or inspect.returncode:
        log.step('Building Caliper 0.7.1 with the official Fabric Gateway connector')
        subprocess.run(['docker','build','-t',IMAGE,str(ROOT/'benchmarks/caliper')],check=True,timeout=900)
    image=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]
    log.step('Checking the initial ledger size (no fixture writes)')
    initial=ledger_count(runtime)
    config=benchmark_config(a,run_id);write(log.folder/'benchmark.json',config);write(log.folder/'network.json',network_config(cfg))
    shutil.copytree(ROOT/'benchmarks/caliper/workload',log.folder/'workload')
    msp=runtime/'role-users-1/official1@org1.example.com/msp'
    tls=runtime/'crypto/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt'
    for p in (msp/'signcerts/cert.pem',msp/'keystore/key.pem',tls):
        if not p.is_file():raise RuntimeError('deployment client identity or TLS certificate missing')
    command=['docker','run','--rm','--name',name,'--network',cfg['project']+'_default',
        '--user',f'{os.getuid()}:{os.getgid()}', '-e','HOME=/tmp','-e','NPM_CONFIG_CACHE=/tmp/npm',
        '-e','CALIPER_REPORTPATH=/caliper/workspace/report.html',
        '-v',f'{log.folder}:/caliper/workspace','-v',f'{msp}:/identity:ro','-v',f'{tls}:/peer-tls.pem:ro',IMAGE,
        'launch','manager','--caliper-workspace','/caliper/workspace','--caliper-benchconfig','benchmark.json',
        '--caliper-networkconfig','network.json','--caliper-flow-only-test']
    timeout_s=2*max(300,2*a.count+180)+180
    log.step(f'Caliper: {a.count} write attempts, then {a.count} full-scan reads; {a.workers} workers; pending load {a.load}')
    before=time.monotonic()
    try:
        process=subprocess.Popen(command)
        seen=set();position=0
        while process.poll() is None:
            with log.path.open() as diagnostics:
                diagnostics.seek(position);lines=diagnostics.readlines();position=diagnostics.tell()
            for line in lines:
                match=re.search(r'Started round (\d+) \(([^)]+)\)',line)
                if match and match[2] not in seen:
                    seen.add(match[2]);log.step(f'Caliper round {match[1]}/{len(config["test"]["rounds"])}: {match[2]}')
            if time.monotonic()-before>timeout_s:
                raise TimeoutError('Caliper execution exceeded its bounded timeout')
            time.sleep(1)
        if process.returncode:raise RuntimeError('Caliper process failed')
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True,text=True,timeout=20)
        if 'process' in locals():process.wait(timeout=15)
    summary=summarize(log.folder,config['test']['rounds'])
    if len(summary['official_caliper_summary'])!=len(config['test']['rounds']):raise RuntimeError('Caliper HTML summary is incomplete')
    passed=all(r['count_matches'] and r['failed']==0 for r in summary['rounds'])
    final_count=ledger_count(runtime)
    manifest={'result':'passed' if passed else 'completed_with_failures','run_id':run_id,'configuration':cfg,
        'options':vars(a),'initial_report_count':initial,'read_report_count':final_count,
        'read_report_count_source':'Post-run indication, not a snapshot at read start. External writes or late commits after failed acknowledgements may change ledger size.',
        'final_report_count':final_count,'execution_timeout_s':timeout_s,
        'rate_controller':'Official Caliper fixed-load, with per-worker count allocation through TestMessage.setNumberOfTxs',
        'image_id':image['Id'],'image_digests':image.get('RepoDigests',[]),
        'source_sha256':source_manifest(),**summary}
    write(log.folder/'result.json',manifest)
    if not (log.folder/'report.html').exists():raise RuntimeError('Caliper did not produce its HTML report')
    for row in summary['rounds']:
        log.say(f'{row["label"]}: {row["successful_throughput_tps"]:.2f} successful/s; {row["succeeded"]} succeeded, {row["failed"]} failed; {row["attempted"]}/{row["requested"]} attempts')
        if not row['count_matches']:log.say(f'{row["label"]}: transaction count or worker allocation mismatch; see result.json')
    log.say(f'Caliper {manifest["result"]}; HTML and JSON: {log.folder.relative_to(ROOT)}')
    return 0 if passed else 1


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__,allow_abbrev=False,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--count',type=int,default=1000,help='total attempts per operation, shared by all workers')
    p.add_argument('--workers',type=int,default=2,help='Caliper client processes')
    p.add_argument('--load',type=int,default=100,help='target pending transactions across all workers (not a TPS limit)')
    p.add_argument('--name',default='development',help='existing deployment name')
    p.add_argument('--verbose',action='store_true',help='also stream detailed logs to the terminal')
    p.add_argument('--rebuild',action='store_true',help=argparse.SUPPRESS)
    argv=list(sys.argv[1:] if argv is None else argv)
    obsolete={'--duration','--loads','--fixtures','--repeats'}
    if any(arg.split('=',1)[0] in obsolete for arg in argv):
        p.error('duration, loads, fixtures and repeats were replaced by --count N --workers W --load L; each run now performs writes, then reads')
    a=p.parse_args(argv)
    if min(a.count,a.workers,a.load)<1 or max(a.count,a.workers,a.load)>2**53-1:
        p.error('count, workers and load must be positive JavaScript-safe integers')
    if a.workers>a.count:p.error('workers must be <= count; use --workers 1 for --count 1')
    if a.load<a.workers:p.error('load must be >= workers')
    return a,p


def main():
    a,p=parse_args()
    runtime=runtime_path(a.name)
    if not (runtime/'experiment.json').is_file():p.error('deployment not found; run ./startup.sh first')
    cfg=json.loads((runtime/'experiment.json').read_text())
    if cfg['backend']!='fabric':p.error('Caliper requires a Fabric deployment')
    configure_client(runtime,cfg)
    with RunLog(ROOT,a.name,'caliper',a.verbose) as log:
        try:
            with benchmark_lock(runtime):return run(a,runtime,cfg,log)
        except BaseException as exc:
            write(log.folder/'failure.json',{'result':'failed','stage':log.stage,'error_type':type(exc).__name__})
            raise


if __name__=='__main__':
    try:sys.exit(main())
    except KeyboardInterrupt:print('Cancelled.',file=sys.stderr);sys.exit(130)
    except Exception as exc:print(f'Caliper failed: {exc}',file=sys.stderr);sys.exit(1)
