"""Resume missing evaluation stages without overwriting the stopped campaign."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT, write


def main(performance_only=False):
    out = ROOT / 'results/repair'
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%m%d%H%M%S')
    status_path = out / f'resume-{run_id}.json'
    records = []
    cpu = str(ROOT / 'repair/.venv/bin/python')

    def task(name, args, timeout=7200):
        log = out / 'campaign-logs' / f'resume-{run_id}-{name}.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        row = dict(task=name, status='running', command=args,
                   started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                   log=str(log.relative_to(ROOT)))
        records.append(row)
        write(status_path, records)
        print('Starting ' + name, flush=True)
        try:
            with log.open('x') as handle:
                result = subprocess.run(args, cwd=ROOT, stdout=handle,
                                        stderr=subprocess.STDOUT, timeout=timeout)
            row.update(status='completed' if result.returncode == 0 else 'failed',
                       returncode=result.returncode)
        except KeyboardInterrupt:
            row.update(status='stopped_by_user',
                       finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
            write(status_path, records)
            raise
        except Exception as exc:
            row.update(status='failed', error=str(exc)[:1000])
        row['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        write(status_path, records)
        print(json.dumps({k: v for k, v in row.items() if k != 'command'}), flush=True)
        return row['status'] == 'completed'

    def rounds(operation, rate, db=None, orgs=None, bounded=False):
        found = []
        for path in out.glob(f'*/runs/{operation}-{rate}tps-*.json'):
            name = path.parent.parent.name
            if not name.startswith(('dbverified-', 'dbcomplete-', 'dbfinal-',
                                    'scancomplete-', 'scanfinal-',
                                    'scaleverified-', 'scalefinal-')):
                continue
            data = json.loads(path.read_text())
            exp = data.get('experiment', {})
            if db and exp.get('database') != db:
                continue
            if orgs and exp.get('orgs') != orgs:
                continue
            if bounded and 'generator_policy' not in data['summary']:
                continue
            found.append(path)
        return found

    for db in ('leveldb', 'couchdb'):
        missing = max(0, 3 - len(rounds('write', 100, db, 3)))
        if missing:
            task('database-' + db, [sys.executable, 'benchmarks/matrix.py',
                 '--suite', 'database', '--databases', db, '--prefix',
                 f'dbfinal-{run_id}-{db}', '--duration', '60', '--repeats', str(missing)])
    missing = max(0, 3 - len(rounds('scan', 50, 'couchdb', 3, bounded=True)))
    if missing:
        task('bounded-scan', [sys.executable, 'benchmarks/matrix.py', '--suite',
             'database', '--databases', 'couchdb', '--operations', 'scan',
             '--prefix', f'scanfinal-{run_id}', '--duration', '60', '--repeats', str(missing)])
    for n in (5, 10, 15, 20):
        missing = max(0, 3 - len(rounds('write', 25, 'leveldb', n)))
        if missing:
            task(f'scaling-{n}', [sys.executable, 'benchmarks/matrix.py', '--suite',
                 'scaling', '--org-counts', str(n), '--prefix', f'scalefinal-{run_id}-n{n}',
                 '--duration', '60', '--repeats', str(missing)])

    if performance_only:
        print('Performance resume status: ' + str(status_path.relative_to(ROOT)), flush=True)
        return int(any(row['status'] != 'completed' for row in records))

    comparison = {'fabric': 0, 'sqlite-reference': 0}
    for path in out.glob('comparison-*-manifest.json'):
        for row in json.loads(path.read_text()):
            if row.get('status') == 'completed':
                comparison[row['backend']] += 1
    if min(comparison.values()) < 3:
        task('central-comparison', [sys.executable, 'benchmarks/compare.py', '--prefix',
             'comparison-' + run_id, '--samples', '10', '--repeats', '3'])
    for quant in ('none', 'int8'):
        target = out / f'model-{quant}.json'
        if not target.exists():
            task('model-' + quant, [cpu, 'benchmarks/evaluate_model.py',
                 '--quantization', quant, '--samples', '90', '--output',
                 str(target.relative_to(ROOT))], timeout=1800)
    ready = task('demonstration-up', [sys.executable, 'blockchain/scripts/deployment.py', 'up',
                 '--name', 'demonstration', '--port-base', '18000'])
    if ready:
        task('demonstration-smoke', [sys.executable, 'blockchain/scripts/deployment.py', 'smoke',
             '--name', 'demonstration'])
        task('demonstration-security', [sys.executable, 'benchmarks/security.py',
             '--name', 'demonstration'])
        task('real-kafka-pipeline', [cpu, 'benchmarks/pipeline.py', '--name',
             'demonstration', '--samples', '3'], timeout=900)
        task('retention-revocation', [sys.executable, 'benchmarks/retention_check.py',
             '--name', 'demonstration'], timeout=600)
    task('regression', [sys.executable, 'benchmarks/campaign.py', '--phase', 'regression'])
    print('Resume status: ' + str(status_path.relative_to(ROOT)), flush=True)
    return int(any(row['status'] != 'completed' for row in records))


if __name__ == '__main__':
    raise SystemExit(main())
