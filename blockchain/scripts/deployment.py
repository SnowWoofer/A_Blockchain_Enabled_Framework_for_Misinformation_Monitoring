#!/usr/bin/env python3
"""Isolated v3 Fabric experiments. No global container cleanup or shared state.

Each name owns one Compose project, generated config, keys, volumes and ports.
No Docker socket is mounted into a container. Existing deployments are untouched.
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

import yaml

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BIN=ROOT/'blockchain/fabric-samples/bin'
GO=ROOT/'blockchain/chaincode/misinformation/go'
RUNTIME=ROOT/'blockchain/runtime'
LEGACY_RUNTIME=ROOT/'experiments/runtime'


def runtime_path(name):
    if not re.fullmatch('[a-z][a-z0-9-]{0,39}', name):
        raise ValueError('name must be lowercase alphanumeric/hyphen, beginning with a letter')
    current=RUNTIME/name;legacy=LEGACY_RUNTIME/name
    if current.exists() and legacy.exists():
        raise ValueError('deployment name exists in both runtime directories; resolve explicitly')
    return legacy if legacy.exists() else current



def command(args,**kwargs):
    try:
        return subprocess.run([str(x) for x in args],check=True,**kwargs)
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
        # Captured command diagnostics belong in the active run log too.
        for output in (getattr(exc,'stdout',None),getattr(exc,'stderr',None)):
            if output:
                if isinstance(output,bytes):output=output.decode(errors='replace')
                print(output[-8000:],file=sys.stderr,flush=True)
        raise


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(value if isinstance(value,str) else json.dumps(value,indent=2)+'\n')


def source_manifest():
    roots=['apps/blockchain_gateway/app/v1-0-0/api','apps/blockchain_gateway/app/v1-0-0/src',
        'apps/fabric_gateway/src','apps/ipfs_gateway/app','apps/flagging-engine/app','apps/submission-worker/app',
        'apps/claim-ingest-worker/app','apps/fact-checking-service/app','blockchain/chaincode/misinformation/go','blockchain/scripts','benchmarks']
    entries={}
    for root in roots:
        for path in sorted((ROOT/root).rglob('*')):
            if not path.is_file() or any(p in ('runtime','vendor','__pycache__') for p in path.parts):continue
            if path.suffix not in ('.py','.go','.js','.json','.txt','.mod','.sum','.yaml','.yml','.sh') and path.name!='Dockerfile':continue
            entries[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    entries['startup.sh']=hashlib.sha256((ROOT/'startup.sh').read_bytes()).hexdigest()
    return entries


def http(url,body=None,key=None,timeout=65):
    headers={'Content-Type':'application/json'}
    if key:headers['X-API-Key']=key
    req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'HTTP {exc.code}: '+exc.read().decode()[:2000]) from exc


def compose(runtime,*args,capture=False,env=None,timeout=None):
    cfg=json.loads((runtime/'experiment.json').read_text())
    if args and args[0]=='up':
        config_path=runtime/'compose.json'
        definition=json.loads(config_path.read_text())
        changed=False
        for service in definition.get('services',{}).values():
            volumes=service.get('volumes',[])
            for i,volume in enumerate(volumes):
                old=f'{ROOT}/experiments/load.js:/benchmark/load.js:ro'
                if volume==old:
                    volumes[i]=f'{ROOT}/benchmarks/load.js:/benchmark/load.js:ro'
                    changed=True
        if changed:
            backup=runtime/'compose.before-tool-relocation.json'
            if not backup.exists():
                with backup.open('x') as handle:
                    backup.chmod(0o600)
                    handle.write(config_path.read_text())
            write(config_path,definition)
            config_path.chmod(0o600)
    return command(['docker','compose','-p',cfg['project'],'-f',runtime/'compose.json',*args],
        capture_output=capture,text=True,env=os.environ|env if env else None,timeout=timeout)


def role_cert(orgdir,role,n):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID,ObjectIdentifier
    ca=x509.load_pem_x509_certificate(next((orgdir/'ca').glob('*-cert.pem')).read_bytes())
    capath=next(p for p in (orgdir/'ca').iterdir() if p.name.endswith('_sk') or p.name=='priv_sk')
    cakey=serialization.load_pem_private_key(capath.read_bytes(),password=None)
    key=ec.generate_private_key(ec.SECP256R1());now=dt.datetime.now(dt.timezone.utc)
    user={'official':'official','fact_checker':'factchecker','observer':'observer'}[role]+str(n)
    out=orgdir/'users'/f'{user}@org{n}.example.com'/'msp'
    cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,user),x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME,'client')]))
          .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
          # Fabric validates the certification path at the leaf's NotBefore.
          # Never backdate a client certificate before its issuing CA exists.
          .not_valid_before(max(now-dt.timedelta(minutes=5),ca.not_valid_before.replace(tzinfo=dt.timezone.utc)))
          .not_valid_after(min(now+dt.timedelta(days=365),ca.not_valid_after.replace(tzinfo=dt.timezone.utc)))
          .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
          .add_extension(x509.KeyUsage(True,False,False,False,False,False,False,False,False),critical=True)
          .add_extension(x509.UnrecognizedExtension(ObjectIdentifier('1.2.3.4.5.6.7.8.1'),json.dumps({'attrs':{'role':role}}).encode()),critical=False)
          .sign(cakey,hashes.SHA256()))
    for part in ('signcerts','keystore','cacerts','tlscacerts'): (out/part).mkdir(parents=True,exist_ok=True)
    (out/'signcerts/cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (out/'keystore/key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    shutil.copytree(orgdir/'msp/cacerts',out/'cacerts',dirs_exist_ok=True)
    shutil.copytree(orgdir/'msp/tlscacerts',out/'tlscacerts',dirs_exist_ok=True)
    shutil.copyfile(orgdir/'msp/config.yaml',out/'config.yaml')


def prepare(a):
    if not re.fullmatch('[a-z][a-z0-9-]{0,39}',a.name):raise ValueError('name must be lowercase alphanumeric/hyphen, beginning with a letter')
    if a.orgs < 3:raise ValueError('orgs must be at least 3')
    runtime=runtime_path(a.name)
    if runtime.exists():raise ValueError('experiment already exists; choose a new name (existing data preserved)')
    runtime.mkdir(parents=True);runtime.chmod(0o700)
    project='misinfo-v3-'+a.name
    cfg={'name':a.name,'project':project,'orgs':a.orgs,'database':a.database,'port_base':a.port_base,
         'backend':a.backend,'fabric_version':'2.5.16','schema_version':3,'channel':'mychannel',
         'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
         'block_timeout':'2s','block_max_messages':10,'endorsement':'2-of-N','review_policy':'strict majority of frozen independent reviewers'}
    cfg['source_sha256']=source_manifest()
    cfg['provisioning_binary_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (BIN/'cryptogen',BIN/'configtxgen',BIN/'osnadmin') if p.exists()}
    write(runtime/'experiment.json',cfg)
    credentials=[{'org':f'org{i}','role':role,'api_key':secrets.token_hex(32)} for i in range(1,a.orgs+1) for role in ('official','fact_checker','observer')]
    write(runtime/'credentials.json',credentials);(runtime/'credentials.json').chmod(0o600)
    write(runtime/'credential-registry.json',[{'org':r['org'],'role':r['role'],'digest':hashlib.sha256(r['api_key'].encode()).hexdigest()} for r in credentials])
    write(runtime/'expiry-credential.json',next(r for r in credentials if r['org']=='org1' and r['role']=='official'))
    (runtime/'expiry-credential.json').chmod(0o600)
    service_key=secrets.token_hex(32);write(runtime/'storage-key',service_key);(runtime/'storage-key').chmod(0o600)
    services={};volumes={}
    # Bind only the public workflow API to loopback. All signers, Kubo APIs and
    # chaincode endpoints remain on the isolated Docker network.
    services['api']={'image':'misinfo-blockchain-gateway:latest',
        'environment':{'CREDENTIALS_FILE':'/run/credentials.json','OFFCHAIN_DB':'/data/objects.db',
            'STORAGE_MODE':'ipfs-required','IPFS_GATEWAY_URL':'http://storage:9101','IPFS_SERVICE_KEY':service_key,
            'LEDGER_BACKEND':a.backend,'REFERENCE_DB':'/data/reference.db','ORGS':','.join(f'org{i}' for i in range(1,a.orgs+1)),
            'SIGNER_URLS':json.dumps({f'org{i}':f'http://signer{i}:9100' for i in range(1,a.orgs+1)}),'ENABLE_EXPIRY':'true','EXPIRY_CREDENTIAL_FILE':'/run/expiry.json'},
        'volumes':[f'{ROOT}/apps/blockchain_gateway/app/v1-0-0/api:/app/api:ro',f'{ROOT}/apps/blockchain_gateway/app/v1-0-0/src:/app/src:ro',f'{runtime}/credential-registry.json:/run/credentials.json:ro',f'{runtime}/expiry-credential.json:/run/expiry.json:ro','api-data:/data'],
        'ports':[f'127.0.0.1:{a.port_base}:8000']}
    volumes['api-data']={}
    # Private network key is present before any Kubo process starts.
    write(runtime/'swarm.key','/key/swarm/psk/1.0.0/\n/base16/\n'+secrets.token_hex(32)+'\n')
    for i in range(3):
        services[f'ipfs{i}']={'image':'ipfs/kubo:latest',
            'restart':'on-failure:3',
            'environment':{'LIBP2P_FORCE_PNET':'1','IPFS_PROFILE':'server','IPFS_PATH':'/data/ipfs','IPFS_TELEMETRY':'off','DO_NOT_TRACK':'1','LIBP2P_TCP_MUX':'false'},
            'volumes':[f'ipfs{i}-data:/data/ipfs',f'{runtime}/swarm.key:/data/ipfs/swarm.key:ro',f'{runtime}/ipfs-init.sh:/container-init.d/001-private.sh:ro']}
        volumes[f'ipfs{i}-data']={}
    write(runtime/'ipfs-init.sh','#!/bin/sh\nset -eu\nipfs bootstrap rm --all\nipfs config --json AutoConf.Enabled false\nipfs config --json Routing.DelegatedRouters "[]"\nipfs config --json DNS.Resolvers "{}"\nipfs config --json Ipns.DelegatedPublishers "[]"\nipfs config --json AutoTLS.Enabled false\nipfs config --json Swarm.AddrFilters "[]"\nipfs config --json Swarm.Transports.Network.Websocket false\nipfs config Routing.Type dht\n')
    (runtime/'ipfs-init.sh').chmod(0o755)
    services['storage']={'image':'misinfo-ipfs-gateway:latest',
        'environment':{'IPFS_API_URL':'http://ipfs0:5001/api/v0','SERVICE_KEY':service_key},
        'volumes':[f'{ROOT}/apps/ipfs_gateway/app:/app/app:ro']}
    # Each managed Kubo node has its own authenticated bridge for retention.
    for i in (1,2):services[f'storage{i}']={**services['storage'],'environment':{'IPFS_API_URL':f'http://ipfs{i}:5001/api/v0','SERVICE_KEY':service_key}}
    if a.backend=='fabric':
        crypto=runtime/'crypto'
        write(runtime/'crypto.yaml',yaml.safe_dump({'OrdererOrgs':[{'Name':'Orderer','Domain':'example.com','EnableNodeOUs':True,'Specs':[{'Hostname':'orderer'}]}],
            'PeerOrgs':[{'Name':f'Org{i}','Domain':f'org{i}.example.com','EnableNodeOUs':True,'Template':{'Count':1},'Users':{'Count':1}} for i in range(1,a.orgs+1)]},sort_keys=False))
        command([BIN/'cryptogen','generate','--config',runtime/'crypto.yaml','--output',crypto],stdout=subprocess.DEVNULL)
        orgs=[]
        for i in range(1,a.orgs+1):
            orgdir=crypto/'peerOrganizations'/f'org{i}.example.com'
            for role in ('official','fact_checker','observer'):role_cert(orgdir,role,i)
            roles=[{'digest':hashlib.sha256(r['api_key'].encode()).hexdigest(),'role':r['role']} for r in credentials if r['org']==f'org{i}']
            write(runtime/f'signer{i}.json',roles)
            policy=lambda suffix:{'Type':'Signature','Rule':f"OR('Org{i}MSP.{suffix}')"}
            orgs.append({'Name':f'Org{i}MSP','ID':f'Org{i}MSP','MSPDir':str(orgdir/'msp'),
                'Policies':{'Readers':policy('member'),'Writers':policy('member'),'Admins':policy('admin'),'Endorsement':policy('peer')},
                'AnchorPeers':[{'Host':f'peer0.org{i}.example.com','Port':7051}]})
            peerroot=orgdir/'peers'/f'peer0.org{i}.example.com'
            env={'FABRIC_CFG_PATH':'/etc/hyperledger/fabric-config','CORE_PEER_ID':f'{project}-peer{i}',
                 'CORE_PEER_ADDRESS':f'peer0.org{i}.example.com:7051','CORE_PEER_LISTENADDRESS':'0.0.0.0:7051',
                 'CORE_PEER_CHAINCODEADDRESS':f'peer0.org{i}.example.com:7052','CORE_PEER_CHAINCODELISTENADDRESS':'0.0.0.0:7052',
                 'CORE_PEER_LOCALMSPID':f'Org{i}MSP','CORE_PEER_MSPCONFIGPATH':'/crypto/msp',
                 'CORE_PEER_TLS_ENABLED':'true','CORE_PEER_TLS_CERT_FILE':'/crypto/tls/server.crt','CORE_PEER_TLS_KEY_FILE':'/crypto/tls/server.key','CORE_PEER_TLS_ROOTCERT_FILE':'/crypto/tls/ca.crt',
                 'CORE_PEER_GOSSIP_EXTERNALENDPOINT':f'peer0.org{i}.example.com:7051',
                 'CORE_PEER_GOSSIP_USELEADERELECTION':'false','CORE_PEER_GOSSIP_ORGLEADER':'true',
                 'CORE_LEDGER_STATE_STATEDATABASE':'goleveldb' if a.database=='leveldb' else 'CouchDB',
                 'FABRIC_LOGGING_SPEC':'WARN','CORE_OPERATIONS_LISTENADDRESS':'0.0.0.0:9443','CORE_METRICS_PROVIDER':'prometheus'}
            if a.database=='couchdb':
                password=secrets.token_hex(24)
                env.update(CORE_LEDGER_STATE_COUCHDBCONFIG_COUCHDBADDRESS=f'couch{i}:5984',CORE_LEDGER_STATE_COUCHDBCONFIG_USERNAME='admin',CORE_LEDGER_STATE_COUCHDBCONFIG_PASSWORD=password)
                services[f'couch{i}']={'image':'couchdb:3.4.2','environment':{'COUCHDB_USER':'admin','COUCHDB_PASSWORD':password},'volumes':[f'couch{i}-data:/opt/couchdb/data']};volumes[f'couch{i}-data']={}
            services[f'peer{i}']={'image':'hyperledger/fabric-peer:2.5.16','environment':env,
                'volumes':[f'{peerroot}:/crypto:ro',f'{runtime}/peer-config:/etc/hyperledger/fabric-config:ro',f'peer{i}-data:/var/hyperledger/production',f'{runtime}:/experiment:ro'],
                'networks':{'default':{'aliases':[f'peer0.org{i}.example.com']}},'command':'peer node start'}
            volumes[f'peer{i}-data']={}
            # Mount only this organisation's users, never its CA/admin keys.
            services[f'signer{i}']={'image':'misinfo-gateway:latest','environment':{'ORG':f'org{i}','CRYPTO_PATH':'/crypto','SIGNER_CREDENTIALS_FILE':'/run/roles.json',
                'PEER_TLS_CA':'/peer-tls-ca.crt','GATEWAY_TARGET':f'peer{i}:7051','GATEWAY_PEER':f'peer0.org{i}.example.com'},
                'volumes':[f'{orgdir}/users:/crypto/users:ro',f'{peerroot}/tls/ca.crt:/peer-tls-ca.crt:ro',f'{runtime}/signer{i}.json:/run/roles.json:ro',f'{ROOT}/apps/fabric_gateway/src:/app/src:ro',f'{ROOT}/benchmarks/load.js:/benchmark/load.js:ro']}
            # Restrict key mount further below to provisioned role users only.
            role_users=runtime/f'role-users-{i}'
            for role in ('official','fact_checker','observer'):
                username={'official':'official','fact_checker':'factchecker','observer':'observer'}[role]+str(i)+f'@org{i}.example.com'
                shutil.copytree(orgdir/'users'/username,role_users/username)
            services[f'signer{i}']['volumes'][0]=f'{role_users}:/crypto/users:ro'
        peerconfig=runtime/'peer-config';peerconfig.mkdir();shutil.copyfile(ROOT/'blockchain/fabric-samples/config/core.yaml',peerconfig/'core.yaml')
        oc=crypto/'ordererOrganizations/example.com'
        ordererroot=oc/'orderers/orderer.example.com'
        implicit={name:{'Type':'ImplicitMeta','Rule':rule} for name,rule in [('Readers','ANY Readers'),('Writers','ANY Writers'),('Admins','MAJORITY Admins')]}
        orderorg={'Name':'OrdererMSP','ID':'OrdererMSP','MSPDir':str(oc/'msp'),'OrdererEndpoints':['orderer.example.com:7050'],
            'Policies':{name:{'Type':'Signature','Rule':f"OR('OrdererMSP.{role}')"} for name,role in [('Readers','member'),('Writers','member'),('Admins','admin')]}}
        config={'Organizations':[orderorg,*orgs],'Profiles':{'Experiment':{'Policies':implicit,'Capabilities':{'V2_0':True},
            'Orderer':{'OrdererType':'etcdraft','EtcdRaft':{'Consenters':[{'Host':'orderer.example.com','Port':7050,'ClientTLSCert':str(ordererroot/'tls/server.crt'),'ServerTLSCert':str(ordererroot/'tls/server.crt')}]},
                'BatchTimeout':'2s','BatchSize':{'MaxMessageCount':10,'AbsoluteMaxBytes':'99 MB','PreferredMaxBytes':'512 KB'},
                'Organizations':[orderorg],'Policies':implicit|{'BlockValidation':{'Type':'ImplicitMeta','Rule':'ANY Writers'}},'Capabilities':{'V2_0':True}},
            'Application':{'Organizations':orgs,'Policies':implicit|{'LifecycleEndorsement':{'Type':'ImplicitMeta','Rule':'MAJORITY Endorsement'},'Endorsement':{'Type':'ImplicitMeta','Rule':'MAJORITY Endorsement'}},'Capabilities':{'V2_5':True}}}}}
        write(runtime/'configtx.yaml',yaml.safe_dump(config,sort_keys=False))
        command([BIN/'configtxgen','-configPath',runtime,'-profile','Experiment','-channelID','mychannel','-outputBlock',runtime/'channel.block'],stdout=subprocess.DEVNULL)
        services['orderer']={'image':'hyperledger/fabric-orderer:2.5.16','environment':{
            'ORDERER_GENERAL_LISTENADDRESS':'0.0.0.0','ORDERER_GENERAL_LISTENPORT':'7050','ORDERER_GENERAL_LOCALMSPID':'OrdererMSP','ORDERER_GENERAL_LOCALMSPDIR':'/crypto/msp',
            'ORDERER_GENERAL_TLS_ENABLED':'true','ORDERER_GENERAL_TLS_PRIVATEKEY':'/crypto/tls/server.key','ORDERER_GENERAL_TLS_CERTIFICATE':'/crypto/tls/server.crt','ORDERER_GENERAL_TLS_ROOTCAS':'[/crypto/tls/ca.crt]',
            'ORDERER_GENERAL_CLUSTER_CLIENTCERTIFICATE':'/crypto/tls/server.crt','ORDERER_GENERAL_CLUSTER_CLIENTPRIVATEKEY':'/crypto/tls/server.key','ORDERER_GENERAL_CLUSTER_ROOTCAS':'[/crypto/tls/ca.crt]',
            'ORDERER_GENERAL_BOOTSTRAPMETHOD':'none','ORDERER_CHANNELPARTICIPATION_ENABLED':'true',
            'ORDERER_ADMIN_LISTENADDRESS':'0.0.0.0:7053','ORDERER_ADMIN_TLS_ENABLED':'true','ORDERER_ADMIN_TLS_CERTIFICATE':'/crypto/tls/server.crt','ORDERER_ADMIN_TLS_PRIVATEKEY':'/crypto/tls/server.key','ORDERER_ADMIN_TLS_CLIENTROOTCAS':'[/crypto/tls/ca.crt]','FABRIC_LOGGING_SPEC':'WARN'},
            'volumes':[f'{ordererroot}:/crypto:ro','orderer-data:/var/hyperledger/production/orderer',f'{runtime}:/experiment:ro'],
            'ports':[f'127.0.0.1:{a.port_base+3}:7053'], 'networks':{'default':{'aliases':['orderer.example.com']}}}
        volumes['orderer-data']={}
        # A static Go service avoids Docker-in-Docker and privileged peer access.
        command(['go','build','-mod=vendor','-o',runtime/'chaincode'],cwd=GO,env=os.environ|{'CGO_ENABLED':'0','GOCACHE':'/tmp/misinfo-repair-go-cache'})
        code=io.BytesIO()
        with tarfile.open(fileobj=code,mode='w:gz') as tar:
            data=json.dumps({'address':'chaincode:9999','dial_timeout':'10s','tls_required':False}).encode();info=tarfile.TarInfo('connection.json');info.size=len(data);tar.addfile(info,io.BytesIO(data))
        package=io.BytesIO()
        with tarfile.open(fileobj=package,mode='w:gz') as tar:
            for name,data in [('metadata.json',json.dumps({'type':'ccaas','label':'misinformation-v3','path':''}).encode()),('code.tar.gz',code.getvalue())]:
                info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
        pkg=package.getvalue();(runtime/'chaincode.tgz').write_bytes(pkg)
        ccid='misinformation-v3:'+hashlib.sha256(pkg).hexdigest();cfg['package_id']=ccid;write(runtime/'experiment.json',cfg)
        cfg['chaincode_binary_sha256']=hashlib.sha256((runtime/'chaincode').read_bytes()).hexdigest()
        cfg['channel_block_sha256']=hashlib.sha256((runtime/'channel.block').read_bytes()).hexdigest()
        write(runtime/'experiment.json',cfg)
        services['chaincode']={'image':'hyperledger/fabric-peer:2.5.16','entrypoint':['/run/chaincode'],
            'environment':{'CHAINCODE_SERVER_ADDRESS':'0.0.0.0:9999','CHAINCODE_ID':ccid},'volumes':[f'{runtime}/chaincode:/run/chaincode:ro']}
    write(runtime/'compose.json',{'services':services,'volumes':volumes})
    (runtime/'compose.json').chmod(0o600)
    print('Prepared',a.name,'in',runtime)
    return runtime


def peer(runtime,i,*args,capture=False):
    return compose(runtime,'exec','-T','-e',f'CORE_PEER_MSPCONFIGPATH=/experiment/crypto/peerOrganizations/org{i}.example.com/users/Admin@org{i}.example.com/msp',f'peer{i}',*args,capture=capture)


def signer_request(runtime,i,key,fn,args=None,query=False):
    spec={'function':fn,'args':args or []};route='/query' if query else '/invoke'
    script="const http=require('http');const r=http.request({host:'signer"+str(i)+"',port:9100,path:'"+route+"',method:'POST',headers:{'Content-Type':'application/json','X-API-Key':process.env.PROVISION_KEY}},s=>{let b='';s.on('data',d=>b+=d);s.on('end',()=>{console.log(b);process.exit(s.statusCode===200?0:1)})});r.on('error',()=>process.exit(1));r.end(JSON.stringify("+json.dumps(spec)+"));"
    for attempt in range(20):
        try:
            response=compose(runtime,'exec','-T','-e','PROVISION_KEY',f'signer{i}','node','-e',script,env={'PROVISION_KEY':key},capture=True)
            return json.loads(response.stdout.strip().splitlines()[-1])
        except subprocess.CalledProcessError as exc:
            if attempt==19:raise RuntimeError(f'{fn} failed: '+(exc.stdout or '')[-1000:]) from exc
            time.sleep(2)


def provision(runtime):
    cfg=json.loads((runtime/'experiment.json').read_text());n=cfg['orgs']
    if cfg['backend']=='fabric':shutil.copy2(BIN/'osnadmin',runtime/'osnadmin')
    compose(runtime,'up','-d')
    if cfg['backend']=='fabric':
        tls=runtime/'crypto/ordererOrganizations/example.com/orderers/orderer.example.com/tls'
        deadline=time.monotonic()+120
        while True:
            try:
                adminargs=['-o','orderer.example.com:7053','--ca-file','/crypto/tls/ca.crt','--client-cert','/crypto/tls/server.crt','--client-key','/crypto/tls/server.key','--channelID','mychannel']
                response=compose(runtime,'exec','-T','orderer','/experiment/osnadmin','channel','list',*adminargs,capture=True).stdout
                if not response.startswith('Status: 200'):
                    joined=compose(runtime,'exec','-T','orderer','/experiment/osnadmin','channel','join',*adminargs,'--config-block','/experiment/channel.block',capture=True).stdout
                    if not joined.startswith('Status: 201'):raise RuntimeError('orderer channel join failed: '+joined[:500])
                    print(joined)
                else:
                    state=json.loads(response[response.index('{'):])
                    if state.get('status')=='active':break
                if time.monotonic()>deadline:raise RuntimeError('orderer channel did not become active')
                time.sleep(2)
            except subprocess.CalledProcessError:
                if time.monotonic()>deadline:raise
                time.sleep(2)
        for i in range(1,n+1):
            ready=False
            for _ in range(60):
                try:
                    listing=compose(runtime,'exec','-T','-e',f'CORE_PEER_MSPCONFIGPATH=/experiment/crypto/peerOrganizations/org{i}.example.com/users/Admin@org{i}.example.com/msp',f'peer{i}','peer','channel','list',capture=True).stdout
                    if 'mychannel' not in listing:peer(runtime,i,'peer','channel','join','-b','/experiment/channel.block')
                    ready=True;break
                except subprocess.CalledProcessError:time.sleep(2)
            if not ready:raise RuntimeError(f'peer{i} failed to join')
            installed=peer(runtime,i,'peer','lifecycle','chaincode','queryinstalled',capture=True).stdout
            if cfg['package_id'] not in installed:peer(runtime,i,'peer','lifecycle','chaincode','install','/experiment/chaincode.tgz')
        policy="OutOf(2,"+','.join(f"'Org{i}MSP.peer'" for i in range(1,n+1))+')'
        orderargs=['-o','orderer.example.com:7050','--ordererTLSHostnameOverride','orderer.example.com','--tls','--cafile','/experiment/crypto/ordererOrganizations/example.com/orderers/orderer.example.com/tls/ca.crt']
        for i in range(1,n+1):
            try:compose(runtime,'exec','-T','-e',f'CORE_PEER_MSPCONFIGPATH=/experiment/crypto/peerOrganizations/org{i}.example.com/users/Admin@org{i}.example.com/msp',f'peer{i}','peer','lifecycle','chaincode','queryapproved','-C','mychannel','-n','misinformation','--sequence','1',capture=True)
            except subprocess.CalledProcessError:peer(runtime,i,'peer','lifecycle','chaincode','approveformyorg',*orderargs,'--channelID','mychannel','--name','misinformation','--version','3.0','--package-id',cfg['package_id'],'--sequence','1','--signature-policy',policy)
        endpoints=[]
        for i in range(1,n+1):endpoints+=['--peerAddresses',f'peer0.org{i}.example.com:7051','--tlsRootCertFiles',f'/experiment/crypto/peerOrganizations/org{i}.example.com/peers/peer0.org{i}.example.com/tls/ca.crt']
        try:compose(runtime,'exec','-T','peer1','peer','lifecycle','chaincode','querycommitted','-C','mychannel','-n','misinformation',capture=True)
        except subprocess.CalledProcessError:peer(runtime,1,'peer','lifecycle','chaincode','commit',*orderargs,'--channelID','mychannel','--name','misinformation','--version','3.0','--sequence','1','--signature-policy',policy,*endpoints)
        # Register through role-bearing identities via authenticated signers.
        credentials=json.loads((runtime/'credentials.json').read_text())
        first_key=next(r['api_key'] for r in credentials if r['org']=='org1' and r['role']=='official')
        registered=signer_request(runtime,1,first_key,'ListRegisteredOrgs',query=True)['result'] or []
        if not registered:
            configured=signer_request(runtime,1,first_key,'SetFoundingOrgLimit',[str(n)])
            print('Founding organisation limit:',configured['result'],flush=True)
        for i in range(1,n+1):
            key=next(r['api_key'] for r in credentials if r['org']==f'org{i}' and r['role']=='official')
            print('Registered:',signer_request(runtime,i,key,'RegisterOrg')['result'],flush=True)
        registered=signer_request(runtime,1,first_key,'ListRegisteredOrgs',query=True)['result'] or []
        if len(registered)!=n:raise RuntimeError('registered membership does not match experiment configuration')
        actual=[]
        for i in range(1,n+1):
            value=compose(runtime,'exec','-T',f'peer{i}','printenv','CORE_LEDGER_STATE_STATEDATABASE',capture=True).stdout.strip()
            expected='goleveldb' if cfg['database']=='leveldb' else 'CouchDB'
            if value!=expected:raise RuntimeError('database configuration mismatch')
            actual.append({'peer':i,'state_database':value})
        write(runtime/'actual-databases.json',actual)
    # All private-swarm configuration is applied by the init hook before the
    # daemon starts. Root CLI config writes against a live repository can leave
    # root-owned locks and break the next non-root daemon restart.
    # Dial every managed IPFS node into every other, without public bootstrap.
    ids=[]
    for i in range(3):
        for attempt in range(30):
            try:
                host='storage' if i==0 else f'storage{i}'
                script="import json,urllib.request;assert json.load(urllib.request.urlopen('http://"+host+":9101/health',timeout=3))['ipfs_available']"
                compose(runtime,'exec','-T','api','python','-c',script,capture=True)
                ids.append(compose(runtime,'exec','-T','--user','ipfs',f'ipfs{i}','ipfs','id','-f','<id>',capture=True).stdout.strip());break
            except subprocess.CalledProcessError:
                if attempt==29:raise
                time.sleep(2)
    for i in range(3):
        for j in range(3):
            if i!=j:compose(runtime,'exec','-T','--user','ipfs',f'ipfs{i}','ipfs','swarm','connect',f'/dns4/ipfs{j}/tcp/4001/p2p/{ids[j]}')
    deadline=time.monotonic()+90
    while True:
        try:
            if http(f'http://127.0.0.1:{cfg["port_base"]}/health')['status']=='ok':break
        except (RuntimeError,OSError):pass
        if time.monotonic()>deadline:raise RuntimeError('API failed to become healthy')
        time.sleep(2)
    out=ROOT/'results/repair'/cfg['name'];out.mkdir(parents=True,exist_ok=True)
    write(out/'configuration.json',cfg)
    if (runtime/'actual-databases.json').exists():shutil.copyfile(runtime/'actual-databases.json',out/'actual-databases.json')
    images=compose(runtime,'images','--format','json',capture=True).stdout
    write(out/'images.json',images)
    if cfg['backend']=='fabric':
        script="const fs=require('fs');const read=p=>JSON.parse(fs.readFileSync('/app/node_modules/'+p+'/package.json','utf8')).version;console.log(JSON.stringify({node:process.version,fabric_gateway:read('@hyperledger/fabric-gateway'),grpc:read('@grpc/grpc-js'),express:read('express')}))"
        write(out/'signer-versions.json',json.loads(compose(runtime,'exec','-T','signer1','node','-e',script,capture=True).stdout))
    print('Ready:',f'http://127.0.0.1:{cfg["port_base"]}')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','up','down','status','logs','benchmark','smoke','restart','renew-identities'])
    p.add_argument('--name',default='development')
    p.add_argument('--orgs',type=int)
    p.add_argument('--database',choices=['leveldb','couchdb'])
    p.add_argument('--backend',choices=['fabric','sqlite-reference'])
    p.add_argument('--port-base',type=int)
    p.add_argument('--services',nargs='*',default=[])
    p.add_argument('--operation',choices=['count','seed','write','point','page','scan'],default='write')
    p.add_argument('--rate',type=float,default=25)
    p.add_argument('--duration',type=int,default=60)
    p.add_argument('--count',type=int,default=0)
    p.add_argument('--repeat',type=int,default=1)
    p.add_argument('--verbose',action='store_true')
    a=p.parse_args();runtime=runtime_path(a.name)
    if runtime.exists() and a.action=='up':
        saved=json.loads((runtime/'experiment.json').read_text())
        for field in ('orgs','database','backend','port_base'):
            value=getattr(a,field)
            if value is not None and value!=saved[field]:
                p.error(f'{field} conflicts with saved deployment; use a fresh --name')
    for field,value in {'orgs':3,'database':'leveldb','backend':'fabric','port_base':18000}.items():
        if getattr(a,field) is None:setattr(a,field,value)
    if a.action not in ('logs','status'):
        from blockchain.scripts.run_support import RunLog
        with RunLog(ROOT,a.name,a.action,a.verbose) as log:
            log.step(f'{a.action}: {a.name}')
            execute(a,runtime,p)
            log.say(f'{a.action}: passed; results in {log.folder.relative_to(ROOT)}')
    else:execute(a,runtime,p)


def execute(a,runtime,p):
    if a.action=='prepare':prepare(a)
    elif a.action=='up':
        if not runtime.exists():prepare(a)
        if a.services:
            known=json.loads((runtime/'compose.json').read_text())['services']
            if any(s not in known for s in a.services):p.error('unknown service')
            compose(runtime,'up','-d',*a.services)
        else:provision(runtime)
    elif a.action=='down':compose(runtime,'down') # volumes deliberately retained
    elif a.action=='renew-identities':
        cfg=json.loads((runtime/'experiment.json').read_text())
        for i in range(1,cfg['orgs']+1):
            orgdir=runtime/'crypto/peerOrganizations'/f'org{i}.example.com'
            for role in ('official','fact_checker','observer'):role_cert(orgdir,role,i)
            for user in ('official','factchecker','observer'):
                username=f'{user}{i}@org{i}.example.com'
                shutil.copytree(orgdir/'users'/username,runtime/f'role-users-{i}'/username,dirs_exist_ok=True)
        print('Renewed role certificates within their issuing CA validity periods; restart signers before use.')
    elif a.action=='logs':compose(runtime,'logs','--tail','40',*a.services)
    elif a.action=='restart':
        known=json.loads((runtime/'compose.json').read_text())['services']
        if not a.services or any(s not in known for s in a.services):p.error('restart requires known --services')
        compose(runtime,'restart',*a.services)
    elif a.action=='smoke':
        from benchmarks.smoke import smoke
        smoke(a.name)
    elif a.action=='benchmark':
        cfg=json.loads((runtime/'experiment.json').read_text())
        spec={'operation':a.operation,'rate':a.rate,'duration':a.duration,'count':a.count,'prefix':f'bench-{time.time_ns()}','maxInflight':500}
        result=compose(runtime,'exec','-T','signer1','node','/benchmark/load.js',json.dumps(spec),capture=True)
        data=json.loads(result.stdout.strip().splitlines()[-1]);data['experiment']=cfg
        if a.operation=='count':print(json.dumps(data));return
        out=ROOT/'results/repair'/a.name/'runs'
        target=out/f'{a.operation}-{a.rate:g}tps-r{a.repeat}-{time.time_ns()}.json'
        write(target,data);print(json.dumps({'file':str(target.relative_to(ROOT)),**data['summary']},indent=2))
    else:compose(runtime,'ps')


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('Cancelled.',file=sys.stderr);sys.exit(130)
    except Exception as exc:print(f'{type(exc).__name__}: operation failed; see the run log.',file=sys.stderr);sys.exit(1)
