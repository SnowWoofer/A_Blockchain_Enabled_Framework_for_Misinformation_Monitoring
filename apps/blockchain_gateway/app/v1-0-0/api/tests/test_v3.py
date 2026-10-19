import concurrent.futures
import datetime as dt
import hashlib
import json
import pytest


def test_plaintext_credential_destinations_rejected(monkeypatch):
    from objects import IpfsStore
    from ledger_v3 import FabricLedger,LedgerError
    from types import SimpleNamespace
    with pytest.raises(ValueError,match='HTTPS'):
        IpfsStore('http://storage:9101')
    monkeypatch.setenv('SIGNER_URLS',json.dumps({'org1':'http://signer1:9100'}))
    with pytest.raises(LedgerError,match='HTTPS'):
        FabricLedger(SimpleNamespace(org='org1'))


def headers(c,org='org1',role='fact_checker'):
    return {'X-API-Key':c.keys[org,role]}


def create(c,msg='example',**changes):
    data=dict(msg_id=msg,label='1',confidence=.9,model_version='test-v1',content='Synthetic example claim',source_platform='test')
    data.update(changes)
    r=c.post('/api/reports',json=data,headers=headers(c));assert r.status_code==200,r.text
    return r.json()['id']


def vote(c,id_,org='org2',outcome='1',reason='Synthetic evidence'):
    return c.post(f'/api/reports/{id_}/fact-check',json={'outcome':outcome,'reasoning':reason,'support':['reference']},headers=headers(c,org))


@pytest.mark.parametrize('path', ['/api/status','/api/orgs','/api/reports'])
def test_read_authentication_required(client,path):
    assert client.get(path).status_code==401
    assert client.get(path,headers={'X-API-Key':'bad'}).status_code==401
    assert client.get(path,headers=headers(client,role='observer')).status_code==200


@pytest.mark.parametrize('path',['/api/auth/register','/api/auth/login','/api/orgs/apply'])
def test_public_identity_claims_removed(client,path):
    assert client.post(path,json={'org':'org1','password':'anything'}).status_code==404


def test_credentials_hashed_and_revocable(client):
    key=client.keys['org1','fact_checker']
    raw=client.store._conn.execute('SELECT digest FROM credentials_v3').fetchall()
    assert key not in [r[0] for r in raw]
    client.store.revoke_key(key)
    assert client.get('/api/orgs',headers={'X-API-Key':key}).status_code==401


def test_submitter_cannot_vote_and_manual_finalisation_removed(client):
    id_=create(client)
    assert vote(client,id_,'org1').status_code==400
    assert vote(client,id_).status_code==200
    assert client.post(f'/api/reports/{id_}/finalize',headers=headers(client,role='official')).status_code==404
    assert client.post(f'/api/reports/{id_}/finalize',headers=headers(client,'org2')).status_code==404


@pytest.mark.parametrize('label',['0','1'])
def test_symmetric_majority_and_verification(client,label):
    id_=create(client)
    assert vote(client,id_,'org2',label).status_code==200
    assert vote(client,id_,'org3',label).status_code==200
    r=client.get(f'/api/reports/{id_}/chain',headers=headers(client)).json()
    assert r['status']=='FINAL' and r['final_label']==label
    doc=client.get(f'/api/reports/{id_}',headers=headers(client)).json()
    assert doc['fact_check_status']=='FINAL' and len(doc['fact_checks'])==2
    result=client.get(f'/api/reports/{id_}/verify',headers=headers(client)).json()
    assert result['verified'] and result['history_verified']


def test_tie_has_no_factual_verdict(client):
    id_=create(client);vote(client,id_,'org2','0');vote(client,id_,'org3','1')
    r=client.get(f'/api/reports/{id_}/chain',headers=headers(client)).json()
    assert r['status']=='DISPUTED' and not r.get('final_label')


def test_duplicate_retry_idempotent_and_conflicting_vote_rejected(client):
    id_=create(client)
    assert create(client)==id_
    assert vote(client,id_).status_code==200
    assert vote(client,id_).status_code==200
    assert vote(client,id_,outcome='0').status_code==400
    h=client.get(f'/api/reports/{id_}/history',headers=headers(client)).json()
    assert len(h)==2
    r=client.post('/api/reports',json={'msg_id':'example','label':'1','confidence':.9,'model_version':'test-v1','content':'Changed','source_platform':'test'},headers=headers(client))
    assert r.status_code==409


def test_role_boundaries(client):
    id_=create(client)
    for path,body in [(f'/api/reports/{id_}/fact-check',{'outcome':'1','reasoning':'reason'}),
                      (f'/api/reports/{id_}/notes',{'note_id':'n','text':'A note'}),
                      (f'/api/orgs/Org4MSP/admission/vote',{'verdict':'1'})]:
        assert client.post(path,json=body,headers=headers(client,role='observer')).status_code==403


@pytest.mark.parametrize('mutation',['claim','reasoning','support','outcome','checker_msp'])
def test_tampering_is_detected(client,mutation):
    id_=create(client);response=vote(client,id_).json()
    rec=client.get(f'/api/reports/{id_}/chain',headers=headers(client)).json()
    cid=rec['claim_cid'] if mutation=='claim' else response['evidence_cid']
    obj=client.store.get_object(cid)
    if mutation=='claim':obj['source']['content']='Tampered'
    else:obj[mutation]=['Tampered'] if mutation=='support' else 'Tampered'
    with client.store._conn:
        client.store._conn.execute('UPDATE objects_v3 SET payload=? WHERE cid=?',(json.dumps(obj).encode(),cid))
    assert client.get(f'/api/reports/{id_}/verify',headers=headers(client)).json()['verified'] is False
    if mutation=='claim':assert client.get(f'/api/reports/{id_}',headers=headers(client)).status_code==409


def test_missing_evidence_never_verifies(client):
    id_=create(client);cid=vote(client,id_).json()['evidence_cid']
    client.store.forget_local(cid,'test retention')
    result=client.get(f'/api/reports/{id_}/verify',headers=headers(client)).json()
    assert not result['verified'] and not result['evidence'][0]['available']


def test_concurrent_reviews_preserve_both_evidence_objects(client):
    id_=create(client)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        responses=list(pool.map(lambda org:vote(client,id_,org),['org2','org3']))
    assert all(r.status_code==200 for r in responses)
    doc=client.get(f'/api/reports/{id_}',headers=headers(client)).json()
    assert {v['checker_msp'] for v in doc['fact_checks']}=={'Org2MSP','Org3MSP'}
    assert client.get(f'/api/reports/{id_}/verify',headers=headers(client)).json()['verified']


def test_pagination_and_status_filter(client):
    ids=[create(client,str(i)) for i in range(5)]
    found=[];bookmark=''
    while True:
        r=client.get('/api/reports',params={'page_size':2,'bookmark':bookmark,'status':'PENDING'},headers=headers(client)).json()
        found.extend(x['id'] for x in r['items']);bookmark=r['next_bookmark']
        if not bookmark:break
    assert len(found)==5 and set(found)==set(ids)
    vote(client,ids[0]);vote(client,ids[0],'org3')
    assert len(client.get('/api/reports',params={'status':'PENDING'},headers=headers(client)).json()['items'])==4
    assert client.get('/api/reports',params={'page_size':201},headers=headers(client)).status_code==422


@pytest.mark.parametrize('status',['PENDING','UNDER_REVIEW','FINAL','DISPUTED','EXPIRED','SUPERSEDED'])
@pytest.mark.parametrize('role',['official','fact_checker'])
def test_notes_preserve_report_in_every_status(client,status,role):
    id_=create(client)
    if status=='UNDER_REVIEW':vote(client,id_)
    elif status in ('FINAL','SUPERSEDED'):
        vote(client,id_);vote(client,id_,'org3')
    elif status=='DISPUTED':
        vote(client,id_,'org2','0');vote(client,id_,'org3','1')
    if status in ('EXPIRED','SUPERSEDED'):
        record=client.ref.get('report:'+id_);record['status']=status
        with client.ref.conn:client.ref.put('report:'+id_,record,'historical-fixture',dt.datetime.now(dt.timezone.utc).isoformat())
    before=client.get(f'/api/reports/{id_}/chain',headers=headers(client)).json()
    history=client.get(f'/api/reports/{id_}/history',headers=headers(client)).json()
    body={'note_id':'clarification','text':'A correction or action/outcome note.','support':['reference']}
    response=client.post(f'/api/reports/{id_}/notes',json=body,headers=headers(client,role=role))
    assert response.status_code==200,response.text
    assert client.post(f'/api/reports/{id_}/notes',json=body,headers=headers(client,role=role)).json()==response.json()
    assert client.post(f'/api/reports/{id_}/notes',json=body|{'text':'Different'},headers=headers(client,role=role)).status_code==409
    rows=client.get(f'/api/reports/{id_}/notes',headers=headers(client,'org2','observer')).json()
    assert len(rows)==1 and rows[0]['verified'] and rows[0]['available']
    assert rows[0]['text']==body['text'] and rows[0]['organisation']=='Org1MSP' and rows[0]['txid']
    assert client.get(f'/api/reports/{id_}/chain',headers=headers(client)).json()==before
    assert client.get(f'/api/reports/{id_}/history',headers=headers(client)).json()==history
    assert client.get(f'/api/reports/{id_}/verify',headers=headers(client)).json()['verified']


@pytest.mark.parametrize('org,role',[('org1','observer'),('org2','official'),('org2','fact_checker')])
def test_note_ownership_and_roles_enforced_by_api_and_ledger(client,org,role):
    from auth_v3 import Identity
    from ledger_v3 import LedgerError
    id_=create(client)
    body={'note_id':'forbidden','text':'A note'}
    assert client.post(f'/api/reports/{id_}/notes',json=body,headers=headers(client,org,role)).status_code==403
    with pytest.raises(LedgerError):
        client.ref.bridge(Identity(org,role,client.keys[org,role])).call('RecordReportNote',id_,'forbidden','cid','a'*64)
    assert client.get(f'/api/reports/{id_}/notes',headers=headers(client)).json()==[]


@pytest.mark.parametrize('body',[{'note_id':' ','text':'Note'}, {'note_id':'n','text':' '},
    {'note_id':'n'}, {'note_id':'n','text':'Note','support':['x']*101},
    {'note_id':'n','text':'Note','outcome':'0'}])
def test_note_validation(client,body):
    id_=create(client)
    assert client.post(f'/api/reports/{id_}/notes',json=body,headers=headers(client)).status_code==422


@pytest.mark.parametrize('mutation',['text','support','organisation','missing'])
def test_note_tampering_and_missing_content_never_verify(client,mutation):
    id_=create(client)
    body={'note_id':'n','text':'Original note','support':['reference']}
    result=client.post(f'/api/reports/{id_}/notes',json=body,headers=headers(client)).json()
    cid=result['evidence_cid']
    if mutation=='missing':client.store.forget_local(cid,'test retention')
    else:
        doc=client.store.get_object(cid);doc[mutation]=['changed'] if mutation=='support' else 'changed'
        with client.store._conn:client.store._conn.execute('UPDATE objects_v3 SET payload=? WHERE cid=?',(json.dumps(doc).encode(),cid))
    response=client.get(f'/api/reports/{id_}/notes',headers=headers(client)).json()[0]
    assert not response['verified'] and response['text'] is None
    assert response['available']==(mutation!='missing')
    assert not client.get(f'/api/reports/{id_}/verify',headers=headers(client)).json()['verified']


def test_concurrent_identical_notes_do_not_duplicate(client):
    id_=create(client);body={'note_id':'n','text':'Retry-safe note'}
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        responses=list(pool.map(lambda _:client.post(f'/api/reports/{id_}/notes',json=body,headers=headers(client)),range(2)))
    assert all(r.status_code==200 for r in responses)
    assert responses[0].json()==responses[1].json()
    assert len(client.get(f'/api/reports/{id_}/notes',headers=headers(client)).json())==1


@pytest.mark.parametrize('doc',[[],{'schema_version':3,'kind':'report_note','note_id':'n','text':42,'support':[]}])
def test_malformed_directly_committed_note_is_unverified_not_a_server_error(client,doc):
    from auth_v3 import Identity
    id_=create(client)
    if isinstance(doc,dict):doc.update(report_id=id_,organisation='Org1MSP')
    cid,digest=client.store.put_object(doc,'report_note')
    client.ref.bridge(Identity('org1','official',client.keys['org1','official'])).call('RecordReportNote',id_,'n',cid,digest)
    response=client.get(f'/api/reports/{id_}/notes',headers=headers(client))
    assert response.status_code==200 and not response.json()[0]['verified']
    assert not client.get(f'/api/reports/{id_}/verify',headers=headers(client)).json()['verified']


@pytest.mark.parametrize('method,suffix',[('post','finalize'),('post','expire'),('post','corrections'),('post','interventions'),('get','interventions')])
def test_removed_routes_absent(client,method,suffix):
    id_=create(client)
    assert getattr(client,method)(f'/api/reports/{id_}/{suffix}',headers=headers(client)).status_code==404
    assert f'/api/reports/{{report_id}}/{suffix}' not in client.get('/openapi.json').json()['paths']


def test_new_supersession_rejected_and_same_text_submissions_allowed(client):
    id_=create(client,'first')
    assert create(client,'another-message')!=id_
    body={'msg_id':'correction','label':'0','confidence':.9,'model_version':'v','content':'Note instead','source_platform':'test','supersedes':id_}
    assert client.post('/api/reports',json=body,headers=headers(client)).status_code==422


def test_historical_correction_and_intervention_evidence_still_verify(client):
    # Seed a record produced by the former implementation. New writes cannot
    # create these links, but historical bytes and commitments remain readable.
    original=create(client,'old');corrected=create(client,'old-correction')
    for rid in (original,corrected):vote(client,rid);vote(client,rid,'org3')
    child=client.ref.get('report:'+corrected)
    claim=client.store.get_object(child['claim_cid']);claim['supersedes']=original
    cid,digest=client.store.put_object(claim,'claim')
    child.update(supersedes=original,claim_cid=cid,content_hash=digest)
    parent=client.ref.get('report:'+original);parent.update(status='SUPERSEDED',superseded_by=corrected)
    legacy={'schema_version':3,'kind':'intervention','report_id':corrected,'organisation':'Org2MSP',
        'intervention_id':'notice','action':'Historical clarification','outcome':'Recorded','support':[]}
    evidence_cid,evidence_hash=client.store.put_object(legacy,'intervention')
    ts=dt.datetime.now(dt.timezone.utc).isoformat()
    with client.ref.conn:
        for seq,raw in client.ref.conn.execute('SELECT seq,v FROM history WHERE k=?',('report:'+corrected,)).fetchall():
            snapshot=json.loads(raw);snapshot.update(supersedes=original,claim_cid=cid,content_hash=digest)
            client.ref.conn.execute('UPDATE history SET v=? WHERE seq=?',(json.dumps(snapshot),seq))
        client.ref.conn.execute('UPDATE state SET v=? WHERE k=?',(json.dumps(child),'report:'+corrected))
        client.ref.put('report:'+original,parent,'old-supersession',ts)
        client.ref.put('intervention:'+corrected+':Org2MSP:notice',{'id':'notice','report_id':corrected,
            'organisation':'Org2MSP','evidence_cid':evidence_cid,'evidence_hash':evidence_hash,
            'timestamp':ts,'txid':'old-intervention'},'old-intervention',ts)
    for rid in (original,corrected):
        assert client.get(f'/api/reports/{rid}',headers=headers(client)).status_code==200
        assert client.get(f'/api/reports/{rid}/verify',headers=headers(client)).json()['verified']
    historical=client.get(f'/api/reports/{corrected}',headers=headers(client,'org3','observer')).json()['legacy_interventions']
    assert len(historical)==1 and historical[0]['verified']
    assert historical[0]['content']['action']=='Historical clarification'
    client.store.forget_local(evidence_cid,'test historical evidence unavailable')
    assert client.get(f'/api/reports/{corrected}',headers=headers(client)).json()['legacy_interventions'][0]['available'] is False
    assert not client.get(f'/api/reports/{corrected}/verify',headers=headers(client)).json()['verified']


def test_expiry_and_late_vote(client):
    now=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc);client.ref.clock=lambda:now
    id_=create(client);vote(client,id_)
    client.ref.clock=lambda:now+dt.timedelta(hours=72)
    assert vote(client,id_,'org3').status_code==400
    import api_v3
    from auth_v3 import Identity
    api_v3.expiry_pass(Identity('org1','official',client.keys['org1','official']))
    assert client.get(f'/api/reports/{id_}/chain',headers=headers(client)).json()['status']=='EXPIRED'


def test_invalid_admission_is_rejected_before_ledger(client):
    assert client.post('/api/orgs/Org4MSP/admission/vote',json={'verdict':'admit'},headers=headers(client,role='official')).status_code==422
    assert client.post('/api/orgs/Org4MSP/admission',json={'org_name':'Fourth','org_type':'ngo'},headers=headers(client,role='official')).status_code==403


def test_ipfs_required_does_not_fall_back(tmp_path):
    from objects import OffChainStore,StorageError
    class Down:
        def add_bytes(self,data):raise StorageError('down')
    store=OffChainStore(str(tmp_path/'db'),ipfs=Down(),mode='ipfs-required')
    with pytest.raises(StorageError):store.put_object({'test':True},'test')
    assert store._conn.execute('SELECT COUNT(*) FROM objects_v3').fetchone()[0]==0
    store.close()


def test_submission_index_rebuilds_from_ledger(client):
    id_=create(client)
    with client.store._conn:
        client.store._conn.execute('DELETE FROM submissions_v3')
    assert create(client)==id_
    assert len(client.get(f'/api/reports/{id_}/history',headers=headers(client)).json())==1
    assert client.store.existing_submission('org1','example')['report_id']==id_


def test_history_snapshot_changes_are_detected(client):
    id_=create(client);vote(client,id_)
    with client.ref.conn:
        row=client.ref.conn.execute('SELECT seq,v FROM history WHERE k=? ORDER BY seq LIMIT 1',('report:'+id_,)).fetchone()
        state=json.loads(row[1]);state['required_votes']=1
        client.ref.conn.execute('UPDATE history SET v=? WHERE seq=?',(json.dumps(state),row[0]))
    assert not client.get(f'/api/reports/{id_}/verify',headers=headers(client)).json()['verified']


def test_reference_restart_cannot_admit_org_by_configuration(client,tmp_path):
    from reference import ReferenceLedger
    path=tmp_path/'separate.db'
    first=ReferenceLedger(path,['org1','org2','org3']);first.conn.close()
    second=ReferenceLedger(path,['org1','org2','org3','org4'])
    assert len(second.rows('org:'))==3
    second.conn.close()
