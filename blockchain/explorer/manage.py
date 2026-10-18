#!/usr/bin/env python3
"""Connect an isolated Explorer dashboard to an existing named Fabric deployment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from blockchain.scripts.deployment import runtime_path,write,command,compose as fabric_compose
from blockchain.scripts.run_support import RunLog

HERE=Path(__file__).resolve().parent


def private_write(path,value):
    write(path,value);path.chmod(0o600)


def connection_profile(runtime,cfg,login):
    profile=json.loads((HERE/'connection-profile/networkConfig.json').read_text())
    profile['name']=cfg['name']
    profile['client']['adminCredential']={'id':login['username'],'password':login['password']}
    profile['channels']={cfg['channel']:{'peers':{}}}
    profile['organizations']={};profile['peers']={}
    for i in range(1,cfg['orgs']+1):
        name=f'peer0.org{i}.example.com'
        tls=runtime/f'crypto/peerOrganizations/org{i}.example.com/peers/{name}/tls/ca.crt'
        profile['channels'][cfg['channel']]['peers'][name]={}
        profile['organizations'][f'Org{i}MSP']={'mspid':f'Org{i}MSP','peers':[name]}
        profile['peers'][name]={'url':f'grpcs://{name}:7051','tlsCACerts':{'pem':tls.read_text()},
            'grpcOptions':{'ssl-target-name-override':name,'hostnameOverride':name}}
    # Explorer calls this field adminPrivateKey, but the mounted identity is
    # an observer client. Neither organisation admins nor CA keys are mounted.
    profile['organizations']['Org1MSP'].update(adminPrivateKey={'path':'/identity/keystore/key.pem'},
        signedCert={'path':'/identity/signcerts/cert.pem'})
    return profile


def prepare(runtime,cfg,port=None):
    if cfg['backend']!='fabric':raise ValueError('Explorer requires a Fabric deployment')
    folder=runtime/'explorer'
    old=json.loads((folder/'state.json').read_text()) if (folder/'state.json').exists() else {}
    selected_port=port if port is not None else old.get('port',cfg['port_base']+80)
    if not 1024<=selected_port<=65535:raise ValueError('Explorer port must be between 1024 and 65535')
    observer=runtime/'role-users-1/observer1@org1.example.com/msp'
    for path in (observer/'keystore/key.pem',observer/'signcerts/cert.pem',runtime/'channel.block'):
        if not path.is_file():raise ValueError('deployment observer identity or channel block missing')
    folder.mkdir(mode=0o700,exist_ok=True);folder.chmod(0o700)
    secrets_path=folder/'secrets.json'
    if secrets_path.exists():private=json.loads(secrets_path.read_text())
    else:
        private={'username':'exploreradmin','password':secrets.token_urlsafe(18),
            'database_password':secrets.token_hex(24),'jwt_secret':secrets.token_hex(32)}
        private_write(secrets_path,private)
    channel_hash=hashlib.sha256((runtime/'channel.block').read_bytes()).hexdigest()[:12]
    identity_hash=hashlib.sha256((observer/'signcerts/cert.pem').read_bytes()).hexdigest()[:12]
    project=cfg['project']+'-explorer'
    state={'name':cfg['name'],'channel':cfg['channel'],'project':project,'port':selected_port,
        'fabric_network':cfg['project']+'_default','observer_msp':str(observer),
        'database_volume':project+'-pgdata-'+channel_hash,
        'wallet_volume':project+'-wallet-'+channel_hash+'-'+identity_hash}
    profile=connection_profile(runtime,cfg,private)
    private_write(folder/'connection-profile/networkConfig.json',profile)
    (folder/'connection-profile').chmod(0o700)
    private_write(folder/'config.json',{'network-configs':{cfg['name']:{'name':cfg['name'],
        'profile':'./connection-profile/networkConfig.json'}},'license':'Apache-2.0'})
    private_write(folder/'explorerconfig.json',{'persistence':'postgreSQL','platforms':['fabric'],
        'postgreSQL':{'host':'explorerdb','port':'5432','database':'fabricexplorer','username':'hppoc','passwd':private['database_password']},
        'sync':{'type':'local','platform':'fabric','blocksSyncTime':'1'},
        'jwt':{'secret':private['jwt_secret'],'expiresIn':'2h'}})
    private_write(folder/'state.json',state)
    return state,private


def explorer_compose(runtime,state,private,*args,timeout=120,capture=False):
    env=os.environ|{'EXPLORER_CONFIG_DIR':str(runtime/'explorer'),'EXPLORER_PORT':str(state['port']),
        'EXPLORER_FABRIC_NETWORK':state['fabric_network'],'EXPLORER_OBSERVER_MSP':state['observer_msp'],
        'EXPLORER_DB_VOLUME':state['database_volume'],'EXPLORER_WALLET_VOLUME':state['wallet_volume'],
        'EXPLORER_DATABASE_PASSWORD':private['database_password']}
    return command(['docker','compose','-p',state['project'],'-f',HERE/'docker-compose.yaml',*args],
        env=env,timeout=timeout,capture_output=capture,text=True)


def request(url,body=None,token=None):
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)


def wait_ready(state,private,timeout=240):
    base=f'http://127.0.0.1:{state["port"]}';until=time.monotonic()+timeout
    while time.monotonic()<until:
        try:
            login=request(base+'/auth/login',{'user':private['username'],'password':private['password'],'network':state['name']})
            if login.get('success'):
                token=login['token'];response=request(base+'/api/channels/info',token=token)
                for channel in response.get('channels',[]):
                    if channel.get('channelname')==state['channel'] and int(channel.get('blocks',0))>0:
                        status=request(base+'/api/status/'+channel['channel_genesis_hash'],token=token)
                        if int(status.get('chaincodeCount',0))>0 and int(status.get('peerCount',0))>0:
                            return {'url':base,'channel':channel,'status':status,'authenticated':True}
        except (OSError,ValueError,KeyError):pass
        time.sleep(2)
    raise RuntimeError(f'Explorer did not authenticate and index the channel within {timeout} seconds; inspect its logs')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',default='up',choices=['up','down','status','logs','credentials'])
    parser.add_argument('--name',default='development');parser.add_argument('--port',type=int)
    parser.add_argument('--verbose',action='store_true');args=parser.parse_args()
    runtime=runtime_path(args.name)
    if not (runtime/'experiment.json').is_file():parser.error('deployment not found; run ./startup.sh first')
    cfg=json.loads((runtime/'experiment.json').read_text())
    if cfg['backend']!='fabric':parser.error('Explorer requires a Fabric deployment')
    if args.port is not None and args.action!='up':parser.error('--port is only used with up')
    if args.action=='up':
        with RunLog(ROOT,args.name,'explorer',args.verbose) as log:
            try:
                log.step('Checking that the selected Fabric peer is running')
                services=fabric_compose(runtime,'ps','--status','running','--services',capture=True,timeout=30).stdout.split()
                if 'peer1' not in services:raise RuntimeError(f'Fabric peer is stopped; run ./startup.sh --name {args.name} first')
                log.step('Generating the Explorer profile from the selected deployment')
                state,private=prepare(runtime,cfg,args.port)
                log.step('Building and starting Explorer with its own database and wallet')
                explorer_compose(runtime,state,private,'up','-d','--build','--force-recreate',timeout=900)
                log.step('Waiting for Explorer authentication and channel indexing')
                result=wait_ready(state,private)
                sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.rglob('*')
                    if p.is_file() and '__pycache__' not in p.parts and (p.suffix in ('.py','.js','.json','.yaml','.sh') or p.name=='Dockerfile')}
                write(log.folder/'result.json',{'result':'passed','deployment':args.name,'state':state,'source_sha256':sources,**result})
                log.say(f'Explorer ready: {result["url"]} (network: {args.name})')
                log.say(f'Show login: ./blockchain/explorer/manage.sh credentials --name {args.name}')
            except BaseException as error:
                write(log.folder/'failure.json',{'result':'failed','stage':log.stage,'error_type':type(error).__name__})
                raise
        return
    folder=runtime/'explorer'
    if not (folder/'state.json').is_file():parser.error('Explorer is not configured; run manage.sh up first')
    state=json.loads((folder/'state.json').read_text());private=json.loads((folder/'secrets.json').read_text())
    if args.action=='credentials':
        print(f'URL: http://127.0.0.1:{state["port"]}\nNetwork: {args.name}\nUsername: {private["username"]}\nPassword: {private["password"]}')
    elif args.action=='status':explorer_compose(runtime,state,private,'ps')
    elif args.action=='logs':explorer_compose(runtime,state,private,'logs','--tail','80')
    else:
        with RunLog(ROOT,args.name,'explorer-down',args.verbose) as log:
            explorer_compose(runtime,state,private,'down')
            log.say('Explorer stopped; dashboard database, wallet and Fabric deployment retained.')


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('Cancelled.',file=sys.stderr);sys.exit(130)
    except Exception as error:
        print(f'Explorer failed ({type(error).__name__}): {error}',file=sys.stderr);sys.exit(1)
