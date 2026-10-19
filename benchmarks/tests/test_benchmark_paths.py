import argparse
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from benchmarks.pipeline import select_samples,wait_kafka,prepare_benchmark_groups
from benchmarks.review import review_to_majority
from benchmarks.caliper.run import benchmark_config,network_config,parse_args,summarize
from blockchain.scripts.run_support import benchmark_lock


def test_older_contract_is_not_silently_restarted_with_new_api(tmp_path):
    from blockchain.scripts import deployment
    (tmp_path/'experiment.json').write_text(json.dumps({'orgs':3,'backend':'fabric'}))
    with patch.object(deployment,'compose') as compose:
        with pytest.raises(ValueError,match='fresh --name'):
            deployment.provision(tmp_path)
        compose.assert_not_called()


@pytest.fixture
def data():return json.loads((ROOT/'data/translated_masked_samples_test.json').read_text())


@pytest.mark.parametrize('count',[1,5,6,7,100,0])
def test_exact_unique_selection(data,count):
    first=select_samples(data,count,100)
    assert len(first)==(count or len(data))
    assert len({(r['language'],r['statement']) for r in first})==len(first)
    assert first==select_samples(data,count,100)


def test_six_samples_unchanged(data):
    first=select_samples(data,6,100)
    assert [(r['language'],r['classification']) for r in first]==[
        ('English','non-misinformation'),('English','misinformation'),
        ('Sepedi','misinformation'),('Sepedi','non-misinformation'),
        ('isiZulu','non-misinformation'),('isiZulu','misinformation')]
    suffixes=['1353_82009a','1353_84e974','1353_ae7df6','1318_e5aff1','1318_f8e46a','1353_5d0068']
    assert all(row['id'].endswith(suffix) for row,suffix in zip(first,suffixes))


@pytest.mark.parametrize('count',[-1,999999])
def test_bad_counts(data,count):
    with pytest.raises(ValueError):select_samples(data,count,100)


def test_duplicate_data_rejected(data):
    with pytest.raises(ValueError):select_samples(data+[data[0]],0,100)


@pytest.mark.parametrize('n',[3,5,21,30])
def test_dynamic_majority(n):
    eligible=[f'Org{i}MSP' for i in range(2,n+1)];threshold=len(eligible)//2+1
    record={'eligible_reviewers':eligible,'required_votes':threshold,'status':'UNDER_REVIEW'}
    keys={f'org{i}':'key' for i in range(1,n+1)};calls=[]
    def request(path,body=None,key=None):
        if body:calls.append(path)
        return record|{'status':'FINAL' if len(calls)>=threshold else 'UNDER_REVIEW'}
    with patch('benchmarks.review.http',side_effect=request):
        assert review_to_majority('/report',record,keys,'1','test')['status']=='FINAL'
    assert len(calls)==threshold


def test_missing_reviewers_rejected_before_write():
    record={'eligible_reviewers':['Org2MSP','Org3MSP','Org4MSP','Org5MSP'],'required_votes':3,'status':'PENDING'}
    with patch('benchmarks.review.http') as request:
        with pytest.raises(RuntimeError):review_to_majority('/report',record,{'org1':'x','org2':'y'},'1','test')
        request.assert_not_called()


def test_kafka_wait_is_bounded():
    with patch('benchmarks.pipeline.time.monotonic',side_effect=[0,0,0,121,121]),patch('benchmarks.pipeline.time.sleep'),patch('benchmarks.pipeline.compose',side_effect=subprocess.TimeoutExpired('probe',1)) as compose:
        with pytest.raises(RuntimeError,match='readiness timeout'):wait_kafka(Path('/tmp'),timeout=120)
    assert compose.call_args.kwargs['timeout']<=16
    assert 'timeout' in compose.call_args.args


def test_benchmark_groups_start_at_existing_end_offsets():
    import asyncio
    from aiokafka import TopicPartition
    from unittest.mock import AsyncMock,Mock
    consumers=[]
    for topic in ('claims.raw','claims.flagged'):
        offsets={TopicPartition(topic,0):23,TopicPartition(topic,1):88}
        consumer=Mock(start=AsyncMock(),stop=AsyncMock(),end_offsets=AsyncMock(return_value=offsets),commit=AsyncMock())
        consumer.assignment.return_value=set(offsets);consumers.append(consumer)
    groups={'claims.raw':'flagging-new-run','claims.flagged':'submission-new-run'}
    with patch('aiokafka.AIOKafkaConsumer',side_effect=consumers) as construct:
        starts=asyncio.run(prepare_benchmark_groups('localhost:18004',groups))
    assert len(starts)==4
    assert {r['group'] for r in starts}==set(groups.values())
    assert [r['offset'] for r in starts]==[23,88,23,88]
    for call,consumer in zip(construct.call_args_list,consumers):
        assert call.kwargs['enable_auto_commit'] is False
        assert call.kwargs['group_id']==groups[call.args[0]]
        consumer.commit.assert_awaited_once_with(consumer.end_offsets.return_value)
        consumer.stop.assert_awaited_once()


def test_benchmark_offset_commit_failure_aborts_and_closes_consumer():
    import asyncio
    from aiokafka import TopicPartition
    from unittest.mock import AsyncMock,Mock
    tp=TopicPartition('claims.raw',0)
    consumer=Mock(start=AsyncMock(),stop=AsyncMock(),end_offsets=AsyncMock(return_value={tp:23}),
        commit=AsyncMock(side_effect=RuntimeError('commit failed')))
    consumer.assignment.return_value={tp}
    with patch('aiokafka.AIOKafkaConsumer',return_value=consumer):
        with pytest.raises(RuntimeError,match='commit failed'):
            asyncio.run(prepare_benchmark_groups('localhost:18004',{'claims.raw':'only-this-run'}))
    consumer.stop.assert_awaited_once()


def test_benchmark_lock(tmp_path):
    with benchmark_lock(tmp_path):
        with pytest.raises(RuntimeError):
            with benchmark_lock(tmp_path):pass
    with benchmark_lock(tmp_path):pass


def test_counted_writes_before_reads_and_dynamic_config():
    args=argparse.Namespace(workers=2,load=100,count=1000)
    config=benchmark_config(args,'test')['test'];rounds=config['rounds']
    assert [r['label'] for r in rounds]==['write','read']
    assert config['workers']['number']==2
    assert all(r['txNumber']==1000 and 'txDuration' not in r for r in rounds)
    assert all(r['rateControl']['type']=='workload/count-fixed-load.js' and 'tps' not in r['rateControl']['opts'] for r in rounds)
    assert all(r['rateControl']['opts']=={'transactionLoad':100,'startTps':25} for r in rounds)
    network=network_config({'name':'test','database':'couchdb','orgs':35,'endorsement':'2-of-N','channel':'mychannel'})
    assert network['info']['Organisations']==35
    assert 'admin' not in network['organizations'][0]['identities']['certificates'][0]


def test_failure_counts_and_peak_selection(tmp_path):
    config=benchmark_config(argparse.Namespace(workers=1,load=10,count=1),'test')['test']['rounds']
    rows=[{'round':'read','worker':0,'success':True,'started_ms':0,'finished_ms':1000,'latency_ms':1000},
          {'round':'write','worker':0,'success':False,'started_ms':0,'finished_ms':500,'latency_ms':500}]
    (tmp_path/'transactions-0.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    result=summarize(tmp_path,config)
    assert result['rounds'][0]['failed']==1
    assert result['highest_observed_error_free']['write'] is None
    assert result['highest_observed_error_free']['read']['successful_throughput_tps']==1


@pytest.mark.parametrize('verbose',[False,True])
def test_logs_capture_subprocess_output(tmp_path,verbose):
    script='''from pathlib import Path
import subprocess
from blockchain.scripts.run_support import RunLog
with RunLog(Path(__import__('sys').argv[1]),'demo','test',verbose=__import__('sys').argv[2]=='true') as log:
    print('verbose-detail',flush=True)
    subprocess.run(['echo','child-detail'],check=True)
    log.say('important-summary')
'''
    p=subprocess.run([sys.executable,'-c',script,str(tmp_path),str(verbose).lower()],cwd=ROOT,capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    assert p.stdout.count('important-summary')==1
    assert ('verbose-detail' in p.stdout)==verbose
    assert ('child-detail' in p.stdout)==verbose
    log=next(tmp_path.rglob('logs.txt')).read_text()
    assert 'verbose-detail' in log and 'child-detail' in log


def test_worker_cleanup_on_startup_failure(tmp_path):
    from types import SimpleNamespace
    from benchmarks.pipeline import run_pipeline
    runtime=tmp_path/'runtime';runtime.mkdir()
    (runtime/'compose.json').write_text('{"services":{},"volumes":{}}')
    (runtime/'credentials.json').write_text('[{"org":"org1","role":"fact_checker","api_key":"test-only"}]')
    workers=[];events=[];worker_groups=[]
    def spawn(*args,**kwargs):
        from unittest.mock import Mock
        events.append('worker');worker_groups.append(kwargs['env'].get('KAFKA_CONSUMER_GROUP'))
        process=Mock();workers.append(process);return process
    seeded={}
    async def seed(broker,groups,tls):events.append('offsets');seeded.update(groups);return []
    async def publish(*args,**kwargs):events.append('probe');return [{'topic':'claims.raw','partition':0,'offset':23}]
    log=SimpleNamespace(folder=tmp_path,step=lambda text:None,say=lambda text:None)
    with patch('benchmarks.pipeline.configure_client',return_value='https://127.0.0.1:18000'),patch('benchmarks.pipeline.kafka_options',return_value={}),patch('benchmarks.pipeline.kafka_service',return_value={}),patch('benchmarks.pipeline.compose'),patch('benchmarks.pipeline.wait_kafka'),patch('benchmarks.pipeline.prepare_benchmark_groups',seed),patch('benchmarks.pipeline.publish',publish),patch('benchmarks.pipeline.subprocess.Popen',side_effect=spawn),patch('benchmarks.pipeline.wait_health',side_effect=RuntimeError('worker failed')):
        with pytest.raises(RuntimeError,match='worker failed'):
            run_pipeline(SimpleNamespace(seed=100,rejection_percent=None,random_percent=0),runtime,{'orgs':3,'database':'leveldb'},18000,[{}],tmp_path/'data.json',log)
    assert events==['offsets','probe','worker','worker','worker']
    assert worker_groups[:2]==[seeded['claims.flagged'],seeded['claims.raw']]
    assert len(workers)==3
    for worker in workers:
        worker.send_signal.assert_called_once();worker.wait.assert_called_once()


def test_caliper_all_failed_latency_placeholder(tmp_path):
    config=benchmark_config(argparse.Namespace(workers=1,load=2,count=1),'test')['test']['rounds']
    (tmp_path/'transactions-0.jsonl').write_text(json.dumps({'round':'write','worker':0,'success':False,'started_ms':0,'finished_ms':1000,'latency_ms':1000})+'\n')
    headers=['Name','Succ','Fail','Send Rate (TPS)','Avg Latency (s)','Min Latency (s)','Max Latency (s)','Throughput (TPS)']
    values=['write','0','1','1.0','-','-','-','999.0']
    (tmp_path/'report.html').write_text('<table><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr><tr>'+''.join('<td>'+v+'</td>' for v in values)+'</tr></table>')
    summary=summarize(tmp_path,config)
    assert summary['rounds'][0]['failed']==1 and summary['rounds'][0]['mean_latency_ms'] is None
    assert summary['highest_observed_error_free']['write'] is None
    assert summary['rounds'][0]['successful_throughput_tps']==0
    assert summary['rounds'][0]['caliper_reported_throughput_tps']==999


def test_caliper_cli_defaults():
    args,_=parse_args([])
    assert (args.count,args.workers,args.load,args.name,args.verbose)==(1000,2,100,'development',False)
    args,_=parse_args(['--count','5','--workers','2','--load','3','--name','test'])
    assert (args.count,args.workers,args.load,args.name)==(5,2,3,'test')
    assert parse_args(['--count','1','--workers','1'])[0].count==1


def test_caliper_empty_official_round_with_placeholder_values(tmp_path):
    config=benchmark_config(argparse.Namespace(workers=2,load=2,count=5),'test')['test']['rounds']
    headers=['Name','Succ','Fail','Send Rate (TPS)','Avg Latency (s)','Min Latency (s)','Max Latency (s)','Throughput (TPS)']
    (tmp_path/'report.html').write_text('<table><tr>'+''.join('<th>'+h+'</th>' for h in headers)+
        '</tr><tr>'+''.join('<td>'+v+'</td>' for v in ['write','0','0','-','-','-','-','-'])+'</tr></table>')
    row=summarize(tmp_path,config)['rounds'][0]
    assert not row['count_matches'] and row['attempted']==0
    assert row['successful_throughput_tps']==0 and row['send_rate_tps']==0


@pytest.mark.parametrize('argv',[
    ['--count','0'],['--count','-1'],['--count','1'],['--workers','0'],
    ['--workers','3','--load','2'],['--load','0'],['--count','1.5'],['--count',str(2**53)],
    ['--duration','5'],['--loads','2','4'],['--fixtures=100'],['--repeats','2'],
    ['--reject-writes'],
])
def test_caliper_invalid_cli_before_start(argv,capsys):
    with pytest.raises(SystemExit) as error:parse_args(argv)
    assert error.value.code==2
    if argv[0].split('=')[0] in ('--duration','--loads','--fixtures','--repeats'):
        assert 'replaced by --count' in capsys.readouterr().err


@pytest.mark.parametrize('allocations,count_matches',[
    ([3,2],True),([2,2],False),([2,3],False),([3,3],False),([5,0],False),([3,1,1],False),
])
def test_caliper_exact_counts_and_worker_allocation(tmp_path,allocations,count_matches):
    config=benchmark_config(argparse.Namespace(workers=2,load=10,count=5),'test')['test']['rounds']
    rows=[{'round':op,'worker':worker,'success':True,'started_ms':0,'finished_ms':1000,'latency_ms':1000}
        for op in ('write','read') for worker,count in enumerate(allocations) for _ in range(count)]
    (tmp_path/'transactions-0.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    summary=summarize(tmp_path,config)
    assert all(row['count_matches']==count_matches for row in summary['rounds'])
    assert all(row['requested']==5 for row in summary['rounds'])
    if not count_matches:assert summary['highest_observed_error_free']=={'read':None,'write':None}


@pytest.mark.parametrize('write_allocation,reject,expected_exit',[
    ([3,2],False,0),([2,2],False,1),([0,0],False,1),([3,2],True,1),
])
def test_caliper_saves_results_and_returns_failure_on_bad_counts_or_rejections(tmp_path,write_allocation,reject,expected_exit):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from benchmarks.caliper import run as caliper
    args,_=parse_args(['--count','5','--workers','2','--load','2'])
    folder=tmp_path/'results';folder.mkdir()
    log=SimpleNamespace(folder=folder,path=folder/'logs.txt',step=Mock(),say=Mock())
    runtime=tmp_path/'runtime'
    for relative in ('role-users-1/official1@org1.example.com/msp/signcerts/cert.pem',
                     'role-users-1/official1@org1.example.com/msp/keystore/key.pem',
                     'crypto/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt'):
        target=runtime/relative;target.parent.mkdir(parents=True,exist_ok=True);target.touch()
    headers=['Name','Succ','Fail','Send Rate (TPS)','Avg Latency (s)','Min Latency (s)','Max Latency (s)','Throughput (TPS)']
    rows=[];tables=[]
    for operation,allocation in [('write',write_allocation),('read',[3,2])]:
        success=operation!='write' or not reject
        for worker,count in enumerate(allocation):
            rows.extend({'round':operation,'worker':worker,'success':success,'started_ms':0,'finished_ms':1000,'latency_ms':1000} for _ in range(count))
        attempts=sum(allocation)
        values=[operation,str(attempts if success else 0),str(0 if success else attempts),'100.0',
            '1' if success else '-','1' if success else '-','1' if success else '-','999.0']
        tables.append('<table><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr><tr>'+''.join('<td>'+v+'</td>' for v in values)+'</tr></table>')
    (folder/'transactions-0.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
    (folder/'report.html').write_text(''.join(tables))
    process=Mock();process.poll.return_value=0;process.returncode=0
    cfg={'project':'test','name':'test','database':'leveldb','orgs':3,'endorsement':'2-of-N','channel':'mychannel'}
    with patch.object(caliper,'ROOT',tmp_path),patch.object(caliper,'ledger_count',side_effect=[0,0 if reject else sum(write_allocation)]),\
         patch.object(caliper,'source_manifest',return_value={}),patch.object(caliper.shutil,'copytree'),\
         patch.object(caliper.subprocess,'check_output',return_value='[{"Id":"test"}]'),\
         patch.object(caliper.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as command,\
         patch.object(caliper.subprocess,'Popen',return_value=process):
        assert caliper.run(args,runtime,cfg,log)==expected_exit
    result=json.loads((folder/'result.json').read_text())
    assert result['result']==('passed' if not expected_exit else 'completed_with_failures')
    assert result['rounds'][0]['attempted']==sum(write_allocation)
    if reject:assert result['rounds'][0]['successful_throughput_tps']==0
    assert command.call_args.args[0][:3]==['docker','rm','-f']
    process.wait.assert_called_once()


def test_existing_configuration_conflict_is_non_mutating(tmp_path):
    from blockchain.scripts import deployment
    (tmp_path/'experiment.json').write_text(json.dumps({'orgs':5,'database':'couchdb','backend':'fabric','port_base':18000}))
    with patch.object(deployment,'runtime_path',return_value=tmp_path),patch.object(deployment,'execute') as execute,patch('sys.argv',['deployment.py','up','--orgs','3']):
        with pytest.raises(SystemExit) as exc:deployment.main()
        assert exc.value.code==2
        execute.assert_not_called()
    assert json.loads((tmp_path/'experiment.json').read_text())['orgs']==5


def test_empty_dataset_is_rejected():
    with pytest.raises(ValueError,match='empty'):select_samples([],0,100)
