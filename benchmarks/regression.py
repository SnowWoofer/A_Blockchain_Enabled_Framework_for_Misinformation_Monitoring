"""Run meaningful regression checks and retain machine-readable outcomes."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,write,source_manifest


def main():
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')
    out=ROOT/'results/repair/validation'/stamp;out.mkdir(parents=True,exist_ok=False)
    checks=[];cpu=str(ROOT/'repair/.venv/bin/python')
    commands=[('contract',['go','test','-mod=vendor','-count=1','-json','./...'],ROOT/'blockchain/chaincode/misinformation/go'),
        ('api',[cpu,'-m','pytest','-q','-p','no:cacheprovider','apps/blockchain_gateway/app/v1-0-0/api/tests'],ROOT),
        ('submission-recovery',[cpu,'-m','pytest','-q','-p','no:cacheprovider','apps/submission-worker/tests'],ROOT),
        ('flagging-delivery',[cpu,'-m','pytest','-q','-p','no:cacheprovider','apps/flagging-engine/tests'],ROOT),
        ('signer-syntax',['node','--check','apps/fabric_gateway/src/secure-server.js'],ROOT),
        ('load-generator-syntax',['node','--check','benchmarks/load.js'],ROOT),
        ('shell-syntax',['bash','-n','startup.sh','blockchain/scripts/deploy.sh','blockchain/scripts/add-orgs.sh'],ROOT)]
    for name,args,cwd in commands:
        proc=subprocess.run(args,cwd=cwd,capture_output=True,text=True,timeout=120,env=os.environ|{'GOCACHE':'/tmp/misinfo-repair-go-cache'})
        output=proc.stdout+proc.stderr;write(out/(name+'.log'),output)
        row={'name':name,'passed':proc.returncode==0,'returncode':proc.returncode,'log':str((out/(name+'.log')).relative_to(ROOT))}
        if name=='contract':
            passed=[]
            for line in proc.stdout.splitlines():
                try:event=json.loads(line)
                except ValueError:continue
                if event.get('Action')=='pass' and event.get('Test'):passed.append(event['Test'])
            row['top_level_tests_passed']=sum('/' not in test for test in passed)
            row['passed_test_events_including_subtests']=len(passed)
        else:
            match=re.search(r'(\d+) passed',output)
            if match:row['tests_passed']=int(match.group(1))
        checks.append(row);print(json.dumps(row),flush=True)
    result={'timestamp':dt.datetime.now(dt.timezone.utc).isoformat(),'result':'passed' if all(r['passed'] for r in checks) else 'failed',
        'checks':checks,'source_sha256':source_manifest(),'scope':'Go contract tests; temporary central-reference API tests; worker delivery/recovery tests; syntax. Deployed Fabric/IPFS/Kafka checks are separate integration evidence.'}
    write(ROOT/'results/repair/validation.json',result)
    return int(result['result']!='passed')
