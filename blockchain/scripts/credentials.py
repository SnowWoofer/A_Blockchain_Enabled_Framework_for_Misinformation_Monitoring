#!/usr/bin/env python3
"""Revoke a provisioned role credential at both API and organisation signer."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from blockchain.scripts.deployment import RUNTIME,compose,write,runtime_path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--name',default='development')
    p.add_argument('--org',required=True);p.add_argument('--role',choices=['official','fact_checker','observer'],required=True)
    a=p.parse_args();runtime=runtime_path(a.name)
    private=json.loads((runtime/'credentials.json').read_text())
    record=next(r for r in private if r['org']==a.org and r['role']==a.role)
    digest=hashlib.sha256(record['api_key'].encode()).hexdigest()
    files=[runtime/'credential-registry.json']
    signer=runtime/f'signer{int(a.org[3:])}.json'
    if signer.exists():files.append(signer)
    for file in files:
        rows=json.loads(file.read_text())
        for row in rows:
            if row['digest']==digest:row['revoked']=True
        # Keep the inode: these files are bind-mounted into running services.
        write(file,rows)
    script="import os;from objects import OffChainStore;s=OffChainStore(os.environ['OFFCHAIN_DB']);s.revoke_digest(__import__('sys').argv[1]);s.close()"
    compose(runtime,'exec','-T','-e','PYTHONPATH=/app/api:/app/src','api','python','-c',script,digest,capture=True)
    print(json.dumps({'org':a.org,'role':a.role,'revoked':True,'scope':'API registry and this organisation signer'}))


if __name__=='__main__':main()
