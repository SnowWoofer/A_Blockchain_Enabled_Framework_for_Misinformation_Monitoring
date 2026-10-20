#!/usr/bin/env python3
"""Exercise authenticated workflow routes and anonymous verdict projections."""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blockchain.scripts.deployment import ROOT,runtime_path,write
from blockchain.scripts.transport import configure_client


def run(name):
    runtime = runtime_path(name)
    cfg = json.loads((runtime / 'experiment.json').read_text())
    rows = json.loads((runtime / 'credentials.json').read_text())
    keys = {(r['org'], r['role']): r['api_key'] for r in rows}
    base = configure_client(runtime, cfg)
    print('Deployment:', cfg['name'], '| organisations:', cfg['orgs'], '|', base)
    checks = []

    def api(method, path, body=None, org='org1', role='fact_checker',
            expected=200, auth=True, bad_key=False):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['X-API-Key'] = 'invalid-test-key' if bad_key else keys[(org, role)]
        request = urllib.request.Request(base + path, method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        result = json.loads(raw) if raw else None
        checks.append({'method':method,'path':path,'status':status,'expected':expected})
        print(method, path, '->', status, '(expected', str(expected) + ')')
        if status != expected:
            print(json.dumps(result, indent=2))
            raise AssertionError('Unexpected API response; inspect this stage')
        return result

    def chain(report_id):
        return api('GET', f'/api/reports/{report_id}/chain')

    def majority(report_id, outcome):
        record = chain(report_id)
        required = record['required_votes']
        assert required == len(record['eligible_reviewers']) // 2 + 1
        reviewers = [org for org, role in keys if role == 'fact_checker'
            and f'Org{org[3:]}MSP' in record['eligible_reviewers']]
        reviewers.sort(key=lambda org: int(org[3:]))
        assert len(reviewers) >= required, 'Missing eligible reviewer credentials'
        for org in reviewers[:required]:
            api('POST', f'/api/reports/{report_id}/fact-check',
                {'outcome': outcome, 'reasoning': 'Scripted synthetic API review',
                 'support': []}, org=org)
        record = chain(report_id)
        assert record['status'] == 'FINAL' and record['final_label'] == outcome
        print('Final verdict:', record['final_label'], '| required votes:', required)

    assert api('GET', '/health', auth=False)['status'] == 'ok'
    assert api('GET', '/api/status', role='observer')['ledger_backend'] == 'fabric'
    api('GET', '/api/orgs', role='observer')
    api('GET', '/api/status', auth=False, expected=401)
    api('GET', '/api/status', bad_key=True, expected=401)
    api('GET', '/api/reports?page_size=201', expected=422)
    api('POST', '/api/reports', {}, expected=422)

    body = {'msg_id': 'manual-api-' + uuid.uuid4().hex, 'label': '1',
        'confidence': 0.9, 'model_version': 'manual-api-test',
        'content': 'Synthetic claim for API workflow testing only.',
        'source_platform': 'manual-test'}
    api('POST', '/api/reports', body, role='observer', expected=403)
    api('POST', '/api/reports', body, role='official', expected=403)
    api('POST', '/api/reports', body, auth=False, expected=401)
    created = api('POST', '/api/reports', body)
    report_id = created['id']
    print('Original report ID:', report_id)
    assert api('POST', '/api/reports', body)['id'] == report_id
    api('POST', '/api/reports', dict(body, content='Conflicting reuse of msg_id'),
        expected=409)
    assert chain(report_id)['status'] == 'PENDING'
    api('GET', f'/api/public/reports/{report_id}', auth=False, expected=404)
    api('GET', '/api/public/reports/missing', auth=False, expected=404)
    api('GET', '/api/public/reports?page_size=201', auth=False, expected=422)
    for suffix in ('', '/chain', '/notes', '/history', '/verify'):
        api('GET', f'/api/reports/{report_id}' + suffix, auth=False, expected=401)
    api('GET', f'/api/reports/{report_id}', role='observer')
    page = api('GET', '/api/reports?' + urllib.parse.urlencode({'page_size': 1}))
    if page['next_bookmark']:
        api('GET', '/api/reports?' + urllib.parse.urlencode(
            {'page_size': 1, 'bookmark': page['next_bookmark']}))
    api('GET', '/api/reports?status=PENDING&page_size=50')

    before = chain(report_id)
    original_history = api('GET', f'/api/reports/{report_id}/history')
    note = {'note_id': 'clarification', 'text': 'Synthetic clarification only.', 'support': []}
    first = api('POST', f'/api/reports/{report_id}/notes', note)
    assert api('POST', f'/api/reports/{report_id}/notes', note) == first
    api('POST', f'/api/reports/{report_id}/notes', note, role='official', expected=403)
    api('POST', f'/api/reports/{report_id}/notes', note, auth=False, expected=401)
    api('POST', f'/api/reports/{report_id}/notes', dict(note, text='Conflicting reuse'), expected=409)
    api('POST', f'/api/reports/{report_id}/notes', note, role='observer', expected=403)
    api('POST', f'/api/reports/{report_id}/notes', note, org='org2', expected=403)
    assert chain(report_id) == before
    assert api('GET', f'/api/reports/{report_id}/history') == original_history

    vote = {'outcome': '1', 'reasoning': 'Synthetic test review', 'support': []}
    api('POST', f'/api/reports/{report_id}/fact-check', vote,
        role='observer', expected=403)
    api('POST', f'/api/reports/{report_id}/fact-check', vote, org='org2', role='official', expected=403)
    api('POST', f'/api/reports/{report_id}/fact-check', vote, auth=False, expected=401)
    api('POST', f'/api/reports/{report_id}/fact-check', vote, expected=400)
    for suffix in ('finalize', 'expire', 'corrections', 'interventions'):
        api('POST', f'/api/reports/{report_id}/' + suffix, role='official', expected=404)
    api('GET', f'/api/reports/{report_id}/interventions', expected=404)
    majority(report_id, '1')
    final = chain(report_id)
    published = api('GET', f'/api/public/reports/{report_id}', auth=False)
    assert published['status'] == 'FINAL' and published['final_label'] == '1'
    assert set(published) == {'id', 'status', 'final_label', 'submitted_by', 'finalized_at', 'required_votes', 'content_hash', 'fact_checks'}
    assert all(set(v) == {'checker_msp', 'outcome', 'timestamp', 'txid', 'evidence_hash'} for v in published['fact_checks'])
    public_page = api('GET', '/api/public/reports?page_size=1', auth=False)
    assert all(row['status'] == 'FINAL' for row in public_page['items'])
    if public_page['next_bookmark']:
        api('GET', '/api/public/reports?' + urllib.parse.urlencode(
            {'page_size': 1, 'bookmark': public_page['next_bookmark']}), auth=False)
    api('POST', f'/api/reports/{report_id}/notes',
        {'note_id': 'outcome', 'text': 'Synthetic action/outcome recorded after review.', 'support': []})
    assert chain(report_id) == final
    notes = api('GET', f'/api/reports/{report_id}/notes', org='org2', role='observer')
    assert len(notes) == 2 and all(row['verified'] and row['available'] for row in notes)
    assert all(row['organisation'] == 'Org1MSP' and row['timestamp'] and row['txid'] for row in notes)
    assert api('GET', f'/api/reports/{report_id}/verify')['verified'] is True

    # A new message ID with the same text creates a separate report, intentionally.
    separate = api('POST', '/api/reports', dict(body, msg_id='separate-' + uuid.uuid4().hex))
    assert separate['id'] != report_id
    api('GET', f'/api/public/reports/{separate["id"]}', auth=False, expected=404)
    api('POST', '/api/reports', dict(body, msg_id='retired-link-' + uuid.uuid4().hex,
        supersedes=report_id), expected=422)
    print('Report API checks PASSED. Report:', report_id, '| separate report:', separate['id'])
    result = {'result':'passed','configuration':cfg,'report_id':report_id,
        'pending_report_id':separate['id'],'checks':checks}
    output = ROOT/'results/repair'/name/('api-check-'+uuid.uuid4().hex+'.json')
    write(output,result)
    print('API evidence:',output.relative_to(ROOT))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    run(parser.parse_args().name)
