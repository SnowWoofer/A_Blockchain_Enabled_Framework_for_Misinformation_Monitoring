import argparse
import io
import json
from pathlib import Path
import sys
import subprocess
from unittest.mock import Mock
import pytest
from benchmarks import test_all as runner


def test_saved_name_is_rejected_before_docker_or_mutation(tmp_path,monkeypatch):
    saved=tmp_path/'existing';saved.mkdir();(saved/'ledger').write_text('preserve')
    monkeypatch.setattr(runner,'runtime_path',lambda name:tmp_path/name)
    preflight=Mock();monkeypatch.setattr(runner,'preflight',preflight)
    with pytest.raises(ValueError,match='existing deployment refused'):
        runner.main(['--name','existing'])
    preflight.assert_not_called()
    assert (saved/'ledger').read_text()=='preserve'
    assert sorted(p.name for p in tmp_path.iterdir())==['existing']


def test_failed_stage_is_recorded_and_stops_execution(tmp_path):
    run=runner.AcceptanceRun(argparse.Namespace(),'fresh',22000,Mock())
    with pytest.raises(RuntimeError,match='exit 7'):
        run.command('deliberate failure',[sys.executable,'-c','raise SystemExit(7)'])
    assert len(run.steps)==1 and not run.steps[0]['passed'] and run.steps[0]['returncode']==7


def test_timeout_terminates_owned_command_and_records_failure(monkeypatch):
    original=runner.subprocess.Popen;processes=[]
    def capture(*args,**kwargs):
        proc=original(*args,**kwargs);processes.append(proc);return proc
    monkeypatch.setattr(runner.subprocess,'Popen',capture)
    run=runner.AcceptanceRun(argparse.Namespace(),'fresh',22000,Mock())
    with pytest.raises(subprocess.TimeoutExpired):
        run.command('timeout',[sys.executable,'-c','import time; time.sleep(60)'],timeout=.05)
    assert processes[0].poll() is not None
    assert not run.steps[0]['passed'] and run.steps[0]['error_type']=='TimeoutExpired'


def test_orphaned_named_volume_is_not_reused(monkeypatch):
    command=Mock(side_effect=[subprocess.CompletedProcess([],0,stdout=''),
                             subprocess.CompletedProcess([],0,stdout='old-ledger-volume\n')])
    monkeypatch.setattr(runner.subprocess,'run',command)
    with pytest.raises(ValueError,match='existing Docker resources refused'):
        runner.require_unused_projects(['fresh'])
    assert command.call_count==2
    assert all('rm' not in call.args[0] for call in command.call_args_list)


def test_restart_reads_use_deployment_ca_instead_of_cached_global_opener(tmp_path,monkeypatch):
    import ssl
    from blockchain.scripts.transport import prepare_tls
    prepare_tls(tmp_path,3)
    key='local-test-fact-checker-credential'
    (tmp_path/'credentials.json').write_text(json.dumps([{'org':'org1','role':'fact_checker','api_key':key}]))
    monkeypatch.setattr(runner,'runtime_path',lambda _:tmp_path)
    monkeypatch.setattr(runner,'configure_client',lambda _:'https://example.invalid')
    opener=Mock()
    opener.open.side_effect=[io.BytesIO(b'{"status":"FINAL"}'),
                              io.BytesIO(b'{"verified":true,"history_verified":true}')]
    build=Mock(return_value=opener)
    monkeypatch.setattr(runner.urllib.request,'build_opener',build)
    monkeypatch.setattr(runner.urllib.request,'urlopen',Mock(side_effect=AssertionError('cached opener used')))
    run=runner.AcceptanceRun(argparse.Namespace(),'fresh',22000,Mock())
    run.persisted_reads('fresh','report')
    context=build.call_args.args[0]._context
    assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    assert context.cert_store_stats()['x509_ca']>=1
    requests=[call.args[0] for call in opener.open.call_args_list]
    assert not requests[0].has_header('X-api-key')
    assert requests[1].get_header('X-api-key')==key
    assert run.steps[-1]['passed']


@pytest.mark.parametrize('successful,keep,expected',[
    (True,True,['fresh-ret']),(True,False,['fresh-ret','fresh']),
    (False,True,['fresh-ret','fresh']),
])
def test_cleanup_only_stops_owned_deployments_and_retains_data(tmp_path,monkeypatch,successful,keep,expected):
    for name in ('fresh','fresh-ret','unrelated'):
        folder=tmp_path/name;folder.mkdir();(folder/'compose.json').write_text('{}')
        (folder/'private-state').write_text('preserve')
    monkeypatch.setattr(runner,'runtime_path',lambda name:tmp_path/name)
    run=runner.AcceptanceRun(argparse.Namespace(keep_running=keep),'fresh',22000,Mock())
    run.created=['fresh','fresh-ret'];run.stop=Mock()
    assert run.cleanup(successful)==[]
    assert [c.args[0] for c in run.stop.call_args_list]==expected
    assert all((tmp_path/name/'private-state').read_text()=='preserve' for name in ('fresh','fresh-ret','unrelated'))
