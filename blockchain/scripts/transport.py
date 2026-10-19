"""Named-deployment TLS material and verified client configuration.

The transport CA key never enters a service container. Each service receives
only its own leaf key/certificate and the public CA. Plain local Kubo/CouchDB
RPC is confined to loopback in the respective service network namespace.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import shutil
import ssl

PROFILE = 'tls-v1'


def _private(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


def prepare_tls(runtime, orgs):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    root = runtime / 'transport'
    root.mkdir(mode=0o700)
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, runtime.name + '-transport-ca')])
    ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-dt.timedelta(minutes=5)).not_valid_after(now+dt.timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    encoding = serialization.Encoding.PEM
    private_format = serialization.PrivateFormat.PKCS8
    _private(root/'ca.key', ca_key.private_bytes(encoding, private_format, serialization.NoEncryption()))
    (root/'ca.crt').write_bytes(ca.public_bytes(encoding))
    services = {'api': ['api', 'localhost'], 'chaincode': ['chaincode'],
                'kafka': ['kafka', 'localhost'], 'kafka-client': [],
                'submission-worker': ['localhost'], 'flagging-engine': ['localhost'],
                'claim-ingest-worker': ['localhost']}
    services.update({f'signer{i}': [f'signer{i}'] for i in range(1, orgs+1)})
    services.update({f'storage{i}': [f'storage{i}', f'ipfs{i}'] + (['storage'] if i == 0 else []) for i in range(3)})
    for name, names in services.items():
        folder = root/name
        folder.mkdir(mode=0o700)
        key = ec.generate_private_key(ec.SECP256R1())
        usages = [ExtendedKeyUsageOID.CLIENT_AUTH] if name == 'kafka-client' else [ExtendedKeyUsageOID.SERVER_AUTH]
        if name == 'kafka': usages.append(ExtendedKeyUsageOID.CLIENT_AUTH)
        builder = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-dt.timedelta(minutes=5)).not_valid_after(now+dt.timedelta(days=180))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage(usages), critical=False))
        if names:
            sans = [x509.DNSName(host) for host in names]
            if 'localhost' in names: sans.append(x509.IPAddress(ipaddress.ip_address('127.0.0.1')))
            builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
        cert = builder.sign(ca_key, hashes.SHA256()).public_bytes(encoding)
        private = key.private_bytes(encoding, private_format, serialization.NoEncryption())
        _private(folder/'server.key', private)
        (folder/'server.crt').write_bytes(cert)
        (folder/'ca.crt').write_bytes(ca.public_bytes(encoding))
        if name in ('kafka', 'kafka-client'): _private(folder/'identity.pem', private+cert)
    shutil.copyfile(root/'kafka-client/identity.pem', root/'kafka/client.pem')
    (root/'kafka/client.pem').chmod(0o600)
    properties = ('security.protocol=SSL\nssl.truststore.type=PEM\n'
        'ssl.truststore.location=/transport/ca.crt\nssl.keystore.type=PEM\n'
        'ssl.keystore.location=/transport/client.pem\n'
        'ssl.endpoint.identification.algorithm=https\nrequest.timeout.ms=5000\ndefault.api.timeout.ms=10000\n')
    _private(root/'kafka/client.properties', properties.encode())


def configure_client(runtime, cfg=None):
    cfg = cfg or json.loads((runtime/'experiment.json').read_text())
    if cfg.get('transport_profile') != PROFILE:
        raise ValueError('This deployment predates transport TLS. Reset its test state or use a fresh --name; restarting does not upgrade it.')
    ca = runtime/'transport/ca.crt'
    if not ca.is_file(): raise ValueError('Deployment transport CA is missing; retain its runtime configuration.')
    os.environ['SSL_CERT_FILE'] = str(ca)
    return f'https://127.0.0.1:{cfg["port_base"]}'


def kafka_options(runtime):
    folder = runtime/'transport/kafka-client'
    context = ssl.create_default_context(cafile=str(folder/'ca.crt'))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(folder/'server.crt'), str(folder/'server.key'))
    return {'security_protocol': 'SSL', 'ssl_context': context}


def secure_services(runtime, cfg, services):
    def mount(service, name):
        service.setdefault('volumes', []).append(f'{runtime}/transport/{name}:/transport:ro')
        service.setdefault('environment', {}).update(SSL_CERT_FILE='/transport/ca.crt')

    api = services['api']
    mount(api, 'api')
    api['command'] = ['uvicorn', '--app-dir', 'api', 'server:app', '--host', '0.0.0.0', '--port', '8000',
        '--ssl-certfile', '/transport/server.crt', '--ssl-keyfile', '/transport/server.key']
    api['environment'].update(IPFS_GATEWAY_URL='https://storage:9101',
        SIGNER_URLS=json.dumps({f'org{i}': f'https://signer{i}:9100' for i in range(1, cfg['orgs']+1)}))
    for i in range(3):
        host = 'storage' if i == 0 else f'storage{i}'
        node = f'ipfs{i}'
        services[node]['networks'] = {'default': {'aliases': [host]}}
        bridge = services[host]
        bridge['network_mode'] = 'service:'+node
        bridge['environment']['IPFS_API_URL'] = 'http://127.0.0.1:5001/api/v0'
        mount(bridge, f'storage{i}')
        bridge['command'] = ['uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '9101',
            '--ssl-certfile', '/transport/server.crt', '--ssl-keyfile', '/transport/server.key']
    with (runtime/'ipfs-init.sh').open('a') as handle:
        handle.write('ipfs config Addresses.API /ip4/127.0.0.1/tcp/5001\n'
                     'ipfs config Addresses.Gateway /ip4/127.0.0.1/tcp/8080\n')
    if cfg['backend'] != 'fabric': return
    for i in range(1, cfg['orgs']+1):
        signer = services[f'signer{i}']
        mount(signer, f'signer{i}')
        signer['environment'].update(TLS_CERT_FILE='/transport/server.crt', TLS_KEY_FILE='/transport/server.key',
            NODE_EXTRA_CA_CERTS='/transport/ca.crt')
        # The Fabric 2.5 CouchDB client uses HTTP. Share the peer's namespace
        # and bind that local RPC to loopback, never to a bridge address.
        services[f'peer{i}']['environment']['CORE_OPERATIONS_LISTENADDRESS'] = '127.0.0.1:9443'
        peer = services[f'peer{i}']
        peer['volumes'] = [v for v in peer['volumes'] if v != f'{runtime}:/experiment:ro']
        peer['volumes'].extend([f'{runtime}/crypto:/experiment/crypto:ro',
            f'{runtime}/chaincode.tgz:/experiment/chaincode.tgz:ro',
            f'{runtime}/channel.block:/experiment/channel.block:ro'])
        if cfg['database'] == 'couchdb':
            services[f'peer{i}']['environment']['CORE_LEDGER_STATE_COUCHDBCONFIG_COUCHDBADDRESS'] = '127.0.0.1:5984'
            ini = runtime/f'couch{i}-loopback.ini'
            ini.write_text('[chttpd]\nbind_address = 127.0.0.1\n[httpd]\nbind_address = 127.0.0.1\n')
            couch = services[f'couch{i}']
            couch['network_mode'] = f'service:peer{i}'
            # The image entrypoint chowns everything below /opt/couchdb and
            # exits on a read-only bind there. Copy the public bind settings
            # into its writable config directory before invoking that entrypoint.
            couch['volumes'].append(f'{ini}:/run/transport.ini:ro')
            couch['command'] = ['/bin/bash','-ec',
                'cp /run/transport.ini /opt/couchdb/etc/local.d/transport.ini; exec /docker-entrypoint.sh /opt/couchdb/bin/couchdb']
    orderer = services['orderer']
    orderer['volumes'] = [v for v in orderer['volumes'] if v != f'{runtime}:/experiment:ro']
    orderer['volumes'].extend([f'{runtime}/osnadmin:/experiment/osnadmin:ro',
        f'{runtime}/channel.block:/experiment/channel.block:ro'])
    mount(services['chaincode'], 'chaincode')
    services['chaincode']['environment'].update(CHAINCODE_TLS_CERT='/transport/server.crt', CHAINCODE_TLS_KEY='/transport/server.key')


def kafka_service(runtime, cfg):
    return {'image': 'apache/kafka:3.9.1', 'environment': {
        'KAFKA_NODE_ID': '1', 'KAFKA_PROCESS_ROLES': 'broker,controller',
        'KAFKA_CONTROLLER_QUORUM_VOTERS': '1@kafka:9093',
        'KAFKA_LISTENERS': 'INTERNAL://:9092,CONTROLLER://:9093,EXTERNAL://:9094',
        'KAFKA_ADVERTISED_LISTENERS': f'INTERNAL://kafka:9092,EXTERNAL://localhost:{cfg["port_base"]+4}',
        'KAFKA_LISTENER_SECURITY_PROTOCOL_MAP': 'INTERNAL:SSL,CONTROLLER:SSL,EXTERNAL:SSL',
        'KAFKA_INTER_BROKER_LISTENER_NAME': 'INTERNAL', 'KAFKA_CONTROLLER_LISTENER_NAMES': 'CONTROLLER',
        'KAFKA_SSL_KEYSTORE_TYPE': 'PEM', 'KAFKA_SSL_KEYSTORE_LOCATION': '/transport/identity.pem',
        'KAFKA_SSL_TRUSTSTORE_TYPE': 'PEM', 'KAFKA_SSL_TRUSTSTORE_LOCATION': '/transport/ca.crt',
        'KAFKA_SSL_CLIENT_AUTH': 'required', 'KAFKA_SSL_ENDPOINT_IDENTIFICATION_ALGORITHM': 'https',
        'KAFKA_SSL_ENABLED_PROTOCOLS': 'TLSv1.2,TLSv1.3',
        'KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR': '1', 'KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR': '1',
        'KAFKA_TRANSACTION_STATE_LOG_MIN_ISR': '1', 'KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS': '0',
        'KAFKA_HEAP_OPTS': '-Xms128m -Xmx384m', 'KAFKA_LOG_DIRS': '/var/lib/kafka/data'},
        'ports': [f'127.0.0.1:{cfg["port_base"]+4}:9094'],
        'volumes': ['kafka-data:/var/lib/kafka/data', f'{runtime}/transport/kafka:/transport:ro']}
