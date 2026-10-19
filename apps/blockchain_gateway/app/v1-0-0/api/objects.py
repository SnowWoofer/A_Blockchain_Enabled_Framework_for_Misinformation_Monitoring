"""Immutable objects and rebuildable indexes; IPFS is mandatory by default."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from report import canonical_json
from auth_v3 import ROLES, key_digest


class StorageError(Exception):
    pass


class IpfsStore:
    def __init__(self, api_url=None, api_key=None):
        self.api_url = (api_url or os.environ.get('IPFS_GATEWAY_URL', 'https://localhost:9101')).rstrip('/')
        if not self.api_url.startswith('https://'):
            raise ValueError('Evidence bridge requires HTTPS')
        self.api_key = api_key or os.environ.get('IPFS_SERVICE_KEY', '')

    def request(self, path, data=None, method='GET'):
        req = urllib.request.Request(self.api_url + path, data=data, method=method,
            headers={'X-Service-Key': self.api_key, 'Content-Type': 'application/octet-stream'})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise StorageError(f'IPFS unavailable: {exc}') from exc

    def is_available(self):
        try:
            return bool(json.loads(self.request('/health')).get('ipfs_available'))
        except (StorageError, ValueError):
            return False

    def add_bytes(self, data):
        try:
            cid = json.loads(self.request('/add', data, 'POST'))['cid']
            if not isinstance(cid,str) or not cid: raise ValueError('empty CID')
            return cid
        except (ValueError,KeyError) as exc:
            raise StorageError('invalid IPFS add response') from exc

    def cat_bytes(self, cid):
        return self.request('/cat/' + urllib.parse.quote(cid, safe=''))


class OffChainStore:
    def __init__(self, db_path='offchain_v3.db', ipfs=None, mode=None):
        Path(db_path).absolute().parent.mkdir(parents=True, exist_ok=True)
        self.mode = mode or os.environ.get('STORAGE_MODE', 'ipfs-required')
        if self.mode not in ('ipfs-required', 'sqlite-development'):
            raise ValueError('STORAGE_MODE must be ipfs-required or sqlite-development')
        self._ipfs = ipfs or IpfsStore()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS credentials_v3 (
                digest TEXT PRIMARY KEY, org TEXT NOT NULL, role TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS objects_v3 (
                cid TEXT PRIMARY KEY, payload BLOB, kind TEXT NOT NULL, created_at REAL NOT NULL,
                retain_until REAL NOT NULL, committed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS submissions_v3 (
                org TEXT NOT NULL, msg_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                report_id TEXT NOT NULL, cid TEXT NOT NULL, PRIMARY KEY(org,msg_id));
            CREATE TABLE IF NOT EXISTS retention_log_v3 (
                cid TEXT NOT NULL, deleted_at REAL NOT NULL, reason TEXT NOT NULL);
        ''')
        self._conn.commit()

    def close(self):
        self._conn.close()

    def provision_key(self, api_key, org, role):
        if len(api_key) < 32:
            raise ValueError('credential must contain at least 32 characters')
        return self.provision_digest(key_digest(api_key),org,role)

    def provision_digest(self,digest,org,role):
        if role not in ROLES or not org.startswith('org') or not org[3:].isdigit():
            raise ValueError('invalid organisation or role')
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('invalid credential digest')
        with self._lock, self._conn:
            existing=self._conn.execute('SELECT org,role FROM credentials_v3 WHERE digest=?',(digest,)).fetchone()
            if existing and (existing['org'],existing['role'])!=(org,role):
                raise ValueError('credential cannot be reassigned')
            self._conn.execute('INSERT INTO credentials_v3(digest,org,role) VALUES(?,?,?) ON CONFLICT(digest) DO NOTHING',
                (digest,org,role))

    def identity_for_key(self, value):
        with self._lock:
            row = self._conn.execute('SELECT org,role FROM credentials_v3 WHERE digest=? AND revoked=0', (key_digest(value),)).fetchone()
        return dict(row) if row else None

    def revoke_key(self, value):
        self.revoke_digest(key_digest(value))

    def revoke_digest(self,digest):
        with self._lock,self._conn:
            self._conn.execute('UPDATE credentials_v3 SET revoked=1 WHERE digest=?',(digest,))

    def ipfs_status(self):
        return {'backend': self.mode, 'ipfs_available': self._ipfs.is_available() if self.mode=='ipfs-required' else False,
                'schema_version':3}

    def put_object(self, payload, kind, retain_days=30):
        data = canonical_json(payload).encode()
        digest = hashlib.sha256(data).hexdigest()
        cid = self._ipfs.add_bytes(data) if self.mode=='ipfs-required' else 'sqlite-sha256-' + digest
        now=time.time()
        with self._lock,self._conn:
            self._conn.execute('INSERT INTO objects_v3(cid,payload,kind,created_at,retain_until) VALUES(?,?,?,?,?) ON CONFLICT(cid) DO NOTHING',
                (cid, data if self.mode=='sqlite-development' else None,kind,now,now+retain_days*86400))
        return cid,digest

    def get_object(self,cid):
        if self.mode=='ipfs-required':
            data=self._ipfs.cat_bytes(cid)
        else:
            with self._lock:
                row=self._conn.execute('SELECT payload FROM objects_v3 WHERE cid=?',(cid,)).fetchone()
            if row is None or row['payload'] is None: raise StorageError('object unavailable')
            data=row['payload']
        try: return json.loads(data)
        except (ValueError,UnicodeError) as exc: raise StorageError('invalid object') from exc

    def mark_committed(self,cid):
        with self._lock,self._conn:
            self._conn.execute('UPDATE objects_v3 SET committed=1 WHERE cid=?',(cid,))

    def reserve_submission(self,org,msg_id,fingerprint,report_id,cid):
        with self._lock,self._conn:
            self._conn.execute('INSERT INTO submissions_v3 VALUES(?,?,?,?,?) ON CONFLICT(org,msg_id) DO NOTHING',
                (org,msg_id,fingerprint,report_id,cid))
            row=self._conn.execute('SELECT * FROM submissions_v3 WHERE org=? AND msg_id=?',(org,msg_id)).fetchone()
        if row['fingerprint']!=fingerprint: raise ValueError('msg_id reused with different content')
        return dict(row)

    def existing_submission(self,org,msg_id):
        with self._lock:
            row=self._conn.execute('SELECT * FROM submissions_v3 WHERE org=? AND msg_id=?',(org,msg_id)).fetchone()
        return dict(row) if row else None

    def retention_candidates(self,now=None):
        with self._lock:
            return [dict(r) for r in self._conn.execute('SELECT cid,kind,committed,retain_until FROM objects_v3 WHERE retain_until<=?',(time.time() if now is None else now,))]

    def forget_local(self,cid,reason):
        if not reason.strip(): raise ValueError('retention reason required')
        with self._lock,self._conn:
            self._conn.execute('DELETE FROM objects_v3 WHERE cid=?',(cid,))
            self._conn.execute('DELETE FROM submissions_v3 WHERE cid=?',(cid,))
            self._conn.execute('INSERT INTO retention_log_v3 VALUES(?,?,?)',(cid,time.time(),reason))
