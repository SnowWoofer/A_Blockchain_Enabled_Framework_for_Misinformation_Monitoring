import json
import os
import ssl
from pathlib import Path
import pytest
from cryptography import x509
from blockchain.scripts.transport import PROFILE,prepare_tls,configure_client,kafka_options,kafka_service,secure_services


@pytest.mark.parametrize('orgs',[3,5,21])
def test_tls_material_and_kafka_identity(tmp_path,orgs,monkeypatch):
    prepare_tls(tmp_path,orgs)
    root=tmp_path/'transport'
    ca=x509.load_pem_x509_certificate((root/'ca.crt').read_bytes())
    for name in ['api','chaincode','kafka',f'signer{orgs}','storage2']:
        cert=x509.load_pem_x509_certificate((root/name/'server.crt').read_bytes())
        cert.verify_directly_issued_by(ca)
        assert (root/name/'server.key').stat().st_mode & 0o077 == 0
    assert (root/'ca.key').stat().st_mode & 0o077 == 0
    cfg={'transport_profile':PROFILE,'port_base':18000}
    monkeypatch.delenv('SSL_CERT_FILE',raising=False)
    assert configure_client(tmp_path,cfg)=='https://127.0.0.1:18000'
    assert Path(os.environ['SSL_CERT_FILE'])==root/'ca.crt'
    tls=kafka_options(tmp_path)
    assert tls['security_protocol']=='SSL'
    assert tls['ssl_context'].check_hostname
    assert tls['ssl_context'].verify_mode==ssl.CERT_REQUIRED
    assert tls['ssl_context'].minimum_version==ssl.TLSVersion.TLSv1_2
    service=kafka_service(tmp_path,cfg)
    assert 'PLAINTEXT' not in json.dumps(service)
    assert service['environment']['KAFKA_SSL_CLIENT_AUTH']=='required'


def test_old_profile_refused_before_client_request(tmp_path):
    with pytest.raises(ValueError,match='Reset'):
        configure_client(tmp_path,{'port_base':18000})


def test_compose_confines_local_rpc_and_never_mounts_ca_key(tmp_path):
    (tmp_path/'ipfs-init.sh').write_text('#!/bin/sh\n')
    cfg={'orgs':3,'backend':'fabric','database':'couchdb'}
    services={'api':{'environment':{},'volumes':[]},'chaincode':{},'orderer':{'volumes':[f'{tmp_path}:/experiment:ro']}}
    for i in range(3):
        services[f'ipfs{i}']={}
        services['storage' if i==0 else f'storage{i}']={'environment':{},'volumes':[]}
    for i in range(1,4):
        services[f'signer{i}']={}
        services[f'peer{i}']={'environment':{},'volumes':[f'{tmp_path}:/experiment:ro']}
        services[f'couch{i}']={'volumes':[]}
    secure_services(tmp_path,cfg,services)
    for i in range(1,4):
        assert services[f'couch{i}']['network_mode']==f'service:peer{i}'
        assert services[f'peer{i}']['environment']['CORE_LEDGER_STATE_COUCHDBCONFIG_COUCHDBADDRESS']=='127.0.0.1:5984'
        assert services[f'couch{i}']['volumes'][-1].endswith(':/run/transport.ini:ro')
        assert 'cp /run/transport.ini' in services[f'couch{i}']['command'][-1]
    assert '127.0.0.1/tcp/5001' in (tmp_path/'ipfs-init.sh').read_text()
    assert services['storage']['network_mode']=='service:ipfs0'
    for service in services.values():
        for volume in service.get('volumes',[]):
            source=volume.split(':')[0]
            assert source!=str(tmp_path)
            assert source!=str(tmp_path/'transport')
            assert 'ca.key' not in source
    assert services['api']['environment']['IPFS_GATEWAY_URL'].startswith('https://')
