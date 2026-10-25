#!/usr/bin/env python3
"""Real Kafka -> checkpoint -> IPFS/Fabric -> scripted independent review trace.

Uses host CPU Python services and the named deployment's isolated Kafka broker.
Run separately from performance benchmarks so model CPU use cannot bias them.
"""
from __future__ import annotations
import argparse
import asyncio
import datetime as dt
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
import hashlib
import json
import os
import random
import statistics
from pathlib import Path
import signal
import subprocess
import time
import uuid
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,RUNTIME,compose,http,write,source_manifest,runtime_path
from blockchain.scripts.transport import configure_client,kafka_options,kafka_service


def wait_health(url,process,timeout=240):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if process.poll() is not None:raise RuntimeError('pipeline service exited; inspect private runtime logs')
        try:
            if http(url+'/health',timeout=3)['status']=='ok':return
        except (RuntimeError,OSError):pass
        time.sleep(1)
    raise RuntimeError('pipeline service readiness timed out')


async def publish(broker,topic,messages,tls=None):
    from aiokafka import AIOKafkaProducer
    producer=AIOKafkaProducer(bootstrap_servers=broker,**(tls or {}))
    offsets=[]
    try:
        await asyncio.wait_for(producer.start(),timeout=15)
        for payload in messages:
            sent=await asyncio.wait_for(producer.send_and_wait(topic,payload),timeout=20)
            offsets.append({'topic':sent.topic,'partition':sent.partition,'offset':sent.offset})
    finally:await asyncio.wait_for(producer.stop(),timeout=5)
    return offsets


async def prepare_benchmark_groups(broker,groups,tls=None):
    """Start only this run's fresh groups at current topic ends, before its probe.

    Production consumers still resume their own committed offsets/earliest.
    Replaying all historical tests into each new benchmark otherwise amplifies
    the scored topic and can time out before reaching the current startup probe.
    """
    from aiokafka import AIOKafkaConsumer
    starts=[]
    for topic,group in groups.items():
        consumer=AIOKafkaConsumer(topic,bootstrap_servers=broker,group_id=group,
            enable_auto_commit=False,auto_offset_reset='earliest',**(tls or {}))
        try:
            await asyncio.wait_for(consumer.start(),timeout=15)
            partitions=consumer.assignment()
            if not partitions:raise RuntimeError('benchmark group has no assigned Kafka partitions: '+topic)
            offsets=await asyncio.wait_for(consumer.end_offsets(partitions),timeout=15)
            await asyncio.wait_for(consumer.commit(offsets),timeout=15)
            starts.extend({'group':group,'topic':tp.topic,'partition':tp.partition,'offset':offset}
                for tp,offset in sorted(offsets.items(),key=lambda item:(item[0].topic,item[0].partition)))
        finally:await asyncio.wait_for(consumer.stop(),timeout=5)
    return starts


from blockchain.scripts.run_support import RunLog,benchmark_lock
from benchmarks.review import review_to_majority


def select_samples(data,count,seed):
    if not data:raise ValueError('dataset is empty')
    if count<0 or count>len(data):raise ValueError('samples must be 0 (all) or no more than the dataset size')
    records=[(r['language'],r['statement']) for r in data]
    if len(records)!=len(set(records)):raise ValueError('dataset contains duplicate language/statement records')
    if count==0:count=len(data)
    if count==6:
        selected=[]
        for i,language in enumerate(('English','Sepedi','isiZulu')):
            for j in range(2):
                label=('non-misinformation','misinformation')[(i+j)%2]
                selected.append(next(r for r in data if r['language']==language and r['classification']==label))
        return selected
    return random.Random(seed).sample(data,count)


def percentage(value):
    try:number=Decimal(value)
    except InvalidOperation:raise argparse.ArgumentTypeError('percentage must be a number from 0 to 100') from None
    if not number.is_finite() or not 0<=number<=100:
        raise argparse.ArgumentTypeError('percentage must be from 0 to 100')
    return float(number)


def rejection_indices(count,percent,seed):
    """Choose sample positions independently of dataset selection, half up."""
    if percent is None:return frozenset()
    number=int((Decimal(count)*Decimal(str(percent))/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return frozenset(random.Random(seed).sample(range(count),number))


def review_label(proposed,dataset_label,controlled,overturn):
    if proposed not in ('0','1'):raise RuntimeError('ledger returned an invalid AI label')
    if not controlled:return dataset_label
    return ('0' if proposed=='1' else '1') if overturn else proposed


def submitter_plan(count,percent,seed,org_count):
    number=int((Decimal(count)*Decimal(str(percent))/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    others=[f'org{i}' for i in range(2,org_count+1)]
    if number and not others:raise ValueError('random submissions require another provisioned organisation')
    # Separate random streams keep this independent of samples and review outcomes.
    positions=set(random.Random(f'submitter-positions:{seed}').sample(range(count),number))
    rng=random.Random(f'submitter-identities:{seed}')
    return [rng.choice(others) if i in positions else 'org1' for i in range(count)]


def report_id_for(org,msg_id):
    return hashlib.sha256((f'Org{org[3:]}MSP\0'+msg_id).encode()).hexdigest()


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--name',default='development');p.add_argument('--samples',type=int,default=6)
    p.add_argument('--seed',type=int,default=100);p.add_argument('--verbose',action='store_true')
    p.add_argument('--rejection-percent',type=percentage,default=None,metavar='P',
        help='script a majority overturn of the AI label for P%% of samples (0..100); remaining samples uphold it. Omit to review using dataset labels as before')
    p.add_argument('--random',dest='random_percent',type=percentage,default=0,metavar='P',
        help='submit P%% of samples using randomly selected other provisioned organisations; remaining samples use Org1 (0..100)')
    return p.parse_args(argv),p


def wait_kafka(runtime,timeout=120):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            compose(runtime,'exec','-T','kafka','timeout','--kill-after=2s','12s',
                '/opt/kafka/bin/kafka-topics.sh','--bootstrap-server','localhost:9092','--command-config','/transport/client.properties','--list',capture=True,
                timeout=min(16,max(.1,deadline-time.monotonic())))
            return
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
            print('Kafka not ready; retrying.',flush=True)
            time.sleep(min(2,max(0,deadline-time.monotonic())))
    raise RuntimeError('Kafka was not ready within the readiness timeout')


def main():
    a,p=parse_args();runtime=runtime_path(a.name)
    if not (runtime/'experiment.json').is_file():p.error('deployment not found; run ./startup.sh first')
    cfg=json.loads((runtime/'experiment.json').read_text());port=cfg['port_base']
    dataset=ROOT/'data/translated_masked_samples_test.json'
    selected=select_samples(json.loads(dataset.read_text()),a.samples,a.seed)
    with RunLog(ROOT,a.name,'feed',a.verbose) as log:
        try:
            with benchmark_lock(runtime):
                run_pipeline(a,runtime,cfg,port,selected,dataset,log)
        except BaseException as exc:
            completed=getattr(log,'traces',[])
            write(log.folder/'failure.json',{'result':'failed','stage':log.stage,'error_type':type(exc).__name__,
                'requested_samples':len(selected),'verified_samples':len(completed),'traces':completed,
                'requested_rejection_percent':a.rejection_percent,'seed':a.seed,
                'requested_random_percent':a.random_percent,
                'worker_log_directory':getattr(log,'worker_logs',None),
                'kafka_group_start_offsets':getattr(log,'group_start_offsets',None),
                'startup_backlog_offset':getattr(log,'backlog_offset',None),
                'note':'Samples not reached after a failed stage are unattempted, not successful.'})
            raise


def run_pipeline(a,runtime,cfg,port,selected,dataset,log):
    log.step(f'Feed: {len(selected)} samples; {cfg["orgs"]} organisations; {cfg["database"]}')
    percent=a.rejection_percent;controlled=percent is not None
    rejected=rejection_indices(len(selected),percent,a.seed)
    submitters=submitter_plan(len(selected),a.random_percent,a.seed,cfg['orgs'])
    other_count=sum(org!='org1' for org in submitters)
    write(log.folder/'submission-plan.json',{'requested_random_percent':a.random_percent,
        'seed':a.seed,'sample_count':len(selected),'submitters':submitters})
    write(log.folder/'review-plan.json',{'mode':'controlled-ai-overturn' if controlled else 'dataset-label',
        'requested_rejection_percent':percent,'seed':a.seed,'sample_count':len(selected),
        'planned_ai_label_overturn_indices':sorted(rejected) if controlled else None})
    log.say(f'Scripted reviews: {len(rejected)}/{len(selected)} AI labels will be overturned; requested {percent:g}%' if controlled
        else 'Scripted reviews: dataset labels (existing default)')
    log.say(f'Submitters: Org1 {len(selected)-other_count}; other organisations {other_count}; requested {a.random_percent:g}%')
    definition=json.loads((runtime/'compose.json').read_text())
    base=configure_client(runtime,cfg);tls=kafka_options(runtime)
    definition['services']['kafka']=kafka_service(runtime,cfg)
    definition['volumes']['kafka-data']={};write(runtime/'compose.json',definition);(runtime/'compose.json').chmod(0o600)
    log.step('Starting Kafka and waiting for readiness')
    compose(runtime,'up','-d','kafka',timeout=90)
    wait_kafka(runtime)
    for topic in ('claims.raw','claims.flagged'):
        compose(runtime,'exec','-T','kafka','timeout','--kill-after=2s','25s',
            '/opt/kafka/bin/kafka-topics.sh','--bootstrap-server','localhost:9092',
            '--command-config','/transport/client.properties','--create','--if-not-exists','--topic',topic,'--partitions','1','--replication-factor','1',capture=True,timeout=30)
    credentials=json.loads((runtime/'credentials.json').read_text());keys={r['org']:r['api_key'] for r in credentials if r['role']=='fact_checker'}
    broker=f'localhost:{port+4}'
    run_id=uuid.uuid4().hex;work=runtime/('pipeline-'+run_id);work.mkdir(mode=0o700);log.worker_logs=str(work)
    message_ids=['test_'+uuid.uuid4().hex for _ in selected] if other_count else None
    routes_file=work/'submission-routes.json'
    if other_count:
        write(routes_file,{'credentials':{org:keys[org] for org in set(submitters)},
            'messages':{msg_id:{'org':org,'source_platform':'test',
                'content_sha256':hashlib.sha256(sample['statement'].encode()).hexdigest()}
                for msg_id,org,sample in zip(message_ids,submitters,selected)}})
        routes_file.chmod(0o600)
    log.step('Preparing fresh benchmark groups at the current Kafka offsets')
    groups={'claims.raw':'flagging-'+run_id,'claims.flagged':'submission-'+run_id}
    group_starts=asyncio.run(prepare_benchmark_groups(broker,groups,tls));log.group_start_offsets=group_starts
    write(log.folder/'kafka-start-offsets.json',group_starts)
    backlog_id='startup-backlog-'+run_id
    backlog_offset=asyncio.run(publish(broker,'claims.raw',[json.dumps({
        'msg_id':backlog_id,'content':'Synthetic backlog delivery check.',
        'source_platform':'test','ingest_timestamp':dt.datetime.now(dt.timezone.utc).isoformat()
    }).encode()],tls))[0];log.backlog_offset=backlog_offset
    log.step('Starting workers and loading the CPU model')
    processes=[];handles=[];started=time.monotonic()
    common=os.environ|{'KAFKA_BOOTSTRAP_SERVERS':broker,'OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2','TOKENIZERS_PARALLELISM':'false',
        'BLOCKCHAIN_API_BASE':base,'HF_HUB_OFFLINE':'1',
        'KAFKA_SSL_CAFILE':str(runtime/'transport/kafka-client/ca.crt'),
        'KAFKA_SSL_CERTFILE':str(runtime/'transport/kafka-client/server.crt'),
        'KAFKA_SSL_KEYFILE':str(runtime/'transport/kafka-client/server.key')}
    def start(app,service_port,extra):
        log=(work/(app+'.log')).open('w');handles.append(log)
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(service_port),
            '--ssl-certfile',str(runtime/'transport'/app/'server.crt'),
            '--ssl-keyfile',str(runtime/'transport'/app/'server.key')],
            cwd=ROOT/'apps'/app,env=common|extra,stdout=log,stderr=subprocess.STDOUT)
        processes.append(proc);return proc
    try:
        submit=start('submission-worker',port+1,{'BLOCKCHAIN_API_KEY':keys['org1'],'OUTPUT_PATH':str(work/'submission-dlq.jsonl'),
            'BENCHMARK_ROUTES_FILE':str(routes_file) if other_count else '',
            'KAFKA_CONSUMER_GROUP':groups['claims.flagged']})
        flag=start('flagging-engine',port+5,{'MODEL_PATH':str(ROOT/'apps/flagging-engine/model'),'MODEL_QUANTIZATION':'int8',
            'TORCH_DEVICE':'cpu','KAFKA_ENABLED':'true','KAFKA_CONSUMER_GROUP':groups['claims.raw'],'DEAD_LETTER_PATH':str(work/'inference-dlq.jsonl')})
        ingest=start('claim-ingest-worker',port+6,{'BENCHMARK_MESSAGE_IDS':'true' if other_count else 'false','SOURCE_PLATFORM':'test'})
        for proc,service_port in ((submit,port+1),(flag,port+5),(ingest,port+6)):
            wait_health(f'https://127.0.0.1:{service_port}',proc)
        startup_s=time.monotonic()-started
        model=http(f'https://127.0.0.1:{port+5}/health')
        backlog_report=hashlib.sha256(('Org1MSP\0'+backlog_id).encode()).hexdigest()
        log.step('Verifying delivery of the message queued before worker startup')
        backlog_wait_started=time.monotonic()
        until=time.monotonic()+120
        while True:
            try:
                backlog=http(base+'/api/reports/'+backlog_report+'/verify',key=keys['org1'])
                if backlog['verified']:break
            except RuntimeError:pass
            if time.monotonic()>until:raise RuntimeError(f'startup message at {backlog_offset} did not reach verified report {backlog_report}; inspect worker logs')
            time.sleep(.2)
        backlog_wait_s=time.monotonic()-backlog_wait_started
        log.step('Processing samples and collecting majority reviews')
        traces=[];log.traces=traces;sample_started=time.perf_counter()
        for index,sample in enumerate(selected):
            org=submitters[index];owner_key=keys[org];msp=f'Org{org[3:]}MSP'
            begin=time.perf_counter()
            body={'claims':[sample['statement']]}
            if message_ids:body['msg_ids']=[message_ids[index]]
            ingested=http(f'https://127.0.0.1:{port+6}/ingest',body)
            msg_id=ingested['msg_ids'][0]
            if message_ids:assert msg_id==message_ids[index],'ingestion changed the planned message ID'
            report_id=report_id_for(org,msg_id)
            path=base+'/api/reports/'+report_id
            until=time.monotonic()+120
            while True:
                try:record=http(path+'/chain',key=owner_key);break
                except RuntimeError:
                    if time.monotonic()>until:raise RuntimeError('claim did not reach ledger')
                    time.sleep(.1)
            anchor_ms=(time.perf_counter()-begin)*1000
            assert record['submitted_by']==msp and msp not in record['eligible_reviewers']
            truth='1' if sample['classification']=='misinformation' else '0'
            proposed=record['inference_label']
            outcome=review_label(proposed,truth,controlled,index in rejected)
            reasoning=('Scripted AI-label overturn for workflow testing; not a factual assessment.' if index in rejected
                else 'Scripted AI-label confirmation for workflow testing; not a factual assessment.') if controlled else 'Scripted synthetic test label; not human review.'
            review_to_majority(path,record,keys,outcome,reasoning)
            record=http(path+'/chain',key=owner_key)
            elapsed=(time.perf_counter()-begin)*1000
            assert record['status']=='FINAL' and record['final_label']==outcome
            assert all(vote['checker_msp']!=msp for vote in record['fact_checks'])
            verify_start=time.perf_counter()
            assert http(path+'/verify',key=owner_key)['verified']
            verification_ms=(time.perf_counter()-verify_start)*1000
            traces.append({'msg_id':msg_id,'report_id':report_id,'language':sample['language'],'source_sample_id':sample['id'],'source_sample_sha256':hashlib.sha256(json.dumps(sample,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                'ingest_to_committed_claim_ms':anchor_ms,'ingest_to_final_verdict_ms':elapsed,'status':record['status'],
                'submitted_by':record['submitted_by'],'proposed_label':proposed,'final_label':record['final_label'],
                'required_votes':record['required_votes'],'verified':True,'verification_ms':verification_ms})
            if len(traces)==1 or len(traces)%10==0 or len(traces)==len(selected):
                log.say(f'Samples verified: {len(traces)}/{len(selected)}')
        sample_elapsed=time.perf_counter()-sample_started
        overturns=sum(row['proposed_label']!=row['final_label'] for row in traces)
        submissions_by_org={f'Org{i}MSP':sum(row['submitted_by']==f'Org{i}MSP' for row in traces)
            for i in range(1,cfg['orgs']+1)}
        assert len(traces)-submissions_by_org['Org1MSP']==other_count
        if controlled:assert overturns==len(rejected),'actual AI-label overturn count differs from the requested plan'
        log.step('Checking backlog and invalid-message recovery (outside sample timings)')
        # Poison messages must reach a durable DLQ while later offsets progress.
        poison={
            'inference-dlq.jsonl':asyncio.run(publish(broker,'claims.raw',[b'{invalid-json'],tls))[0],
            'submission-dlq.jsonl':asyncio.run(publish(broker,'claims.flagged',[json.dumps({'msg_id':'invalid-'+run_id}).encode()],tls))[0]}
        def observed(filename,offset):
            path=work/filename
            if not path.exists():return False
            for line in path.read_text().splitlines():
                try:row=json.loads(line)
                except ValueError:continue
                if row.get('_kafka')==offset:return True
            return False
        until=time.monotonic()+15
        while time.monotonic()<until and not all(observed(n,offset) for n,offset in poison.items()):time.sleep(.2)
        dlq={n:observed(n,offset) for n,offset in poison.items()}
        assert all(dlq.values()),dlq
        recovery=[]
        for topic in ('claims.raw','claims.flagged'):
            msg_id='after-poison-'+topic.replace('.','-')+'-'+run_id
            payload={'msg_id':msg_id,'content':'Synthetic delivery check after a poison message.',
                'source_platform':'test','ingest_timestamp':dt.datetime.now(dt.timezone.utc).isoformat()}
            if topic=='claims.flagged':
                payload.update(flagged=False,confidence=1.0,model_version='synthetic-scored-recovery-check')
            sent=asyncio.run(publish(broker,topic,[json.dumps(payload).encode()],tls))[0]
            report_id=hashlib.sha256(('Org1MSP\0'+msg_id).encode()).hexdigest()
            until=time.monotonic()+120
            while True:
                try:
                    verified=http(base+'/api/reports/'+report_id+'/verify',key=keys['org1'])
                    if verified['verified']:break
                except RuntimeError:pass
                if time.monotonic()>until:raise RuntimeError('valid message after poison was not delivered: '+topic)
                time.sleep(.2)
            recovery.append({'topic':topic,'offset':sent,'report_id':report_id,'verified':True})
        for service_port in (port+1,port+5):
            assert http(f'https://127.0.0.1:{service_port}/health')['status']=='ok'
        result={'result':'passed','configuration':cfg,'run_id':run_id,'worker_log_directory':str(work),'model':model,'traces':traces,'durable_dead_letters':dlq,
            'submission_policy':{'requested_random_percent':a.random_percent,
                'planned_non_org1_submissions':other_count,'actual_non_org1_percent':100*other_count/len(traces),
                'meaning':'Selected samples use another provisioned organisation credential through a private benchmark routing file. Input text and AI inference are unchanged. Recovery probes use Org1 and are excluded from sample counts.'},
            'scripted_review':{'mode':'controlled-ai-overturn' if controlled else 'dataset-label',
                'requested_rejection_percent':percent,'planned_ai_label_overturns':len(rejected) if controlled else None,
                'rounding':'Nearest whole sample; half rounded up. Selected positions are seeded independently of sample selection.',
                'meaning':'Rejection means the majority overturns the AI label. Reports and decisions remain on-chain with status FINAL. Scripted decisions are not human review or model accuracy evidence.'},
            'execution_source_sha256':source_manifest(),'seed':a.seed,'requested_samples':a.samples,
            'source_data_sha256':hashlib.sha256(dataset.read_bytes()).hexdigest(),
            'startup_backlog_delivered':True,'startup_backlog_report_id':backlog_report,
            'kafka_group_start_offsets':group_starts,'startup_backlog_offset':backlog_offset,'startup_backlog_wait_s':backlog_wait_s,
            'poison_offsets':poison,
            'delivery_after_poison':recovery,
            'service_startup_s':startup_s,'service_session_s':time.monotonic()-started,
            'timestamp':dt.datetime.now(dt.timezone.utc).isoformat(),
            'timing_boundary':'HTTP ingest request start -> polling detects committed claim / authoritative final verdict response. Final trace includes scripted votes, excludes integrity verification.',
            'limitations':'Single-node Kafka; CPU int8 checkpoint; scripted reviewers and synthetic data; one workstation; no inference-only accuracy claim.'}
        result['summary']={'samples':len(traces),'succeeded':len(traces),'failed':0,
            'submissions_by_org':submissions_by_org,
            'ai_labels_overturned':overturns,'ai_labels_upheld':len(traces)-overturns,
            'ai_label_rejection_percent':100*overturns/len(traces),
            'sample_window_s':sample_elapsed,'verified_workflows_per_second':len(traces)/sample_elapsed,
            'window_boundary':'First sample ingestion through last evidence verification; sequential scripted workflows; excludes startup and recovery probes.'}
        for field in ('ingest_to_committed_claim_ms','ingest_to_final_verdict_ms','verification_ms'):
            values=sorted(r[field] for r in traces)
            result['summary'][field]={'mean':statistics.mean(values),'p50':statistics.median(values),'p95':values[max(0,__import__('math').ceil(.95*len(values))-1)]}
        write(log.folder/'result.json',result)
        # Keep historical analysis compatible with this new uniquely identified run.
        write(ROOT/'results/repair'/a.name/('pipeline-'+run_id+'.json'),result)
        log.say(f'Feed PASSED: {len(traces)}/{len(selected)} verified; AI labels overturned: {overturns}, upheld: {len(traces)-overturns}; results: {log.folder.relative_to(ROOT)}/result.json')
        log.say('Submissions: '+', '.join(f'{org}={count}' for org,count in submissions_by_org.items() if count))
    finally:
        for proc in processes:proc.send_signal(signal.SIGTERM)
        for proc in processes:
            try:proc.wait(timeout=20)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        for log in handles:log.close()


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('Cancelled.',file=sys.stderr);sys.exit(130)
    except Exception as exc:print(f'Feed failed: {exc}',file=sys.stderr);sys.exit(1)
