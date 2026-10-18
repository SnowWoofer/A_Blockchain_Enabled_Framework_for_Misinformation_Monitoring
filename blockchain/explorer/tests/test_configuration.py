import json
from pathlib import Path
import stat
import sys
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from blockchain.explorer.manage import HERE,connection_profile,prepare,wait_ready


def deployment(root,n=3):
    for i in range(1,n+1):
        ca=root/f'crypto/peerOrganizations/org{i}.example.com/peers/peer0.org{i}.example.com/tls/ca.crt'
        ca.parent.mkdir(parents=True);ca.write_text(f'public TLS CA {i}')
    msp=root/'role-users-1/observer1@org1.example.com/msp'
    for name in ('signcerts/cert.pem','keystore/key.pem'):
        path=msp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
    (root/'channel.block').write_bytes(b'channel one')
    return {'name':'test','project':'misinfo-v3-test','orgs':n,'channel':'mychannel','backend':'fabric','port_base':18000}


@pytest.mark.parametrize('n',[3,5,35])
def test_dynamic_profile_and_only_one_observer_key(tmp_path,n):
    cfg=deployment(tmp_path,n)
    profile=connection_profile(tmp_path,cfg,{'username':'test-login','password':'test-password'})
    assert len(profile['channels']['mychannel']['peers'])==n
    assert len(profile['peers'])==n and len(profile['organizations'])==n
    private_keys=[org['adminPrivateKey']['path'] for org in profile['organizations'].values() if 'adminPrivateKey' in org]
    assert private_keys==['/identity/keystore/key.pem']
    assert 'Admin@' not in json.dumps(profile) and 'priv_sk' not in json.dumps(profile)
    for i in range(1,n+1):
        peer=profile['peers'][f'peer0.org{i}.example.com']
        assert peer['tlsCACerts']['pem']==f'public TLS CA {i}'
        assert peer['url']==f'grpcs://peer0.org{i}.example.com:7051'


def test_restart_reuses_login_database_and_private_files(tmp_path):
    cfg=deployment(tmp_path)
    state,private=prepare(tmp_path,cfg,19080)
    again,same_private=prepare(tmp_path,cfg)
    assert state==again and private==same_private
    assert state['port']==19080
    assert state['fabric_network']=='misinfo-v3-test_default'
    assert state['project']=='misinfo-v3-test-explorer'
    assert all(value not in json.dumps(state) for key,value in private.items() if key!='username')
    folder=tmp_path/'explorer'
    assert stat.S_IMODE(folder.stat().st_mode)==0o700
    assert stat.S_IMODE((folder/'connection-profile').stat().st_mode)==0o700
    assert all(stat.S_IMODE(path.stat().st_mode)==0o600 for path in folder.rglob('*.json'))


def test_identity_or_channel_change_selects_new_volumes_without_deleting_old_state(tmp_path):
    cfg=deployment(tmp_path)
    original,private=prepare(tmp_path,cfg)
    cert=tmp_path/'role-users-1/observer1@org1.example.com/msp/signcerts/cert.pem'
    cert.write_text('renewed observer')
    renewed,same_private=prepare(tmp_path,cfg)
    assert renewed['wallet_volume']!=original['wallet_volume']
    assert renewed['database_volume']==original['database_volume']
    (tmp_path/'channel.block').write_bytes(b'different channel')
    other,_=prepare(tmp_path,cfg)
    assert other['database_volume']!=original['database_volume']
    assert other['wallet_volume']!=renewed['wallet_volume']
    assert same_private==private


@pytest.mark.parametrize('port',[1,65536])
def test_invalid_port_is_rejected_before_writing_state(tmp_path,port):
    cfg=deployment(tmp_path)
    with pytest.raises(ValueError,match='port'):prepare(tmp_path,cfg,port)
    assert not (tmp_path/'explorer').exists()


def test_reference_backend_is_rejected(tmp_path):
    cfg=deployment(tmp_path);cfg['backend']='sqlite-reference'
    with pytest.raises(ValueError,match='Fabric'):prepare(tmp_path,cfg)
    assert not (tmp_path/'explorer').exists()


def test_compose_isolates_database_and_only_mounts_observer_identity():
    config=yaml.safe_load((HERE/'docker-compose.yaml').read_text())
    db=config['services']['explorerdb'];viewer=config['services']['explorer']
    assert 'ports' not in db and db['networks']==['database']
    assert config['networks']['database']['internal'] is True
    assert viewer['ports']==['127.0.0.1:${EXPLORER_PORT:-18080}:8080']
    assert viewer['environment']['DATABASE_PASSWD'].startswith('${EXPLORER_DATABASE_PASSWORD')
    assert any(mount.endswith(':/identity:ro') for mount in viewer['volumes'])
    assert all('/tmp/crypto' not in mount for mount in viewer['volumes'])
    assert all('container_name' not in service for service in config['services'].values())


def test_ready_requires_authentication_channel_blocks_and_chaincode():
    state={'port':18080,'name':'test','channel':'mychannel'};private={'username':'user','password':'test-only'}
    responses=[{'success':True,'token':'secret-test-token'},
        {'channels':[{'channelname':'mychannel','blocks':5,'channel_genesis_hash':'genesis'}]},
        {'chaincodeCount':'1','peerCount':'3','txCount':'6','latestBlock':'5'}]
    with patch('blockchain.explorer.manage.request',side_effect=responses):result=wait_ready(state,private)
    assert result['authenticated'] and result['status']['latestBlock']=='5'
    assert 'secret-test-token' not in json.dumps(result) and 'test-only' not in json.dumps(result)


def test_empty_chaincode_list_is_not_reported_ready():
    responses=[{'success':True,'token':'test'},
        {'channels':[{'channelname':'mychannel','blocks':5,'channel_genesis_hash':'genesis'}]},
        {'chaincodeCount':'0','peerCount':'3'}]
    with patch('blockchain.explorer.manage.request',side_effect=responses),patch('blockchain.explorer.manage.time.sleep'),\
         patch('blockchain.explorer.manage.time.monotonic',side_effect=[0,0,2]):
        with pytest.raises(RuntimeError,match='within 1 seconds'):
            wait_ready({'port':18080,'name':'test','channel':'mychannel'},{'username':'user','password':'test'},timeout=1)
