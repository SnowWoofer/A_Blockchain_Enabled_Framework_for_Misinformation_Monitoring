import concurrent.futures
import datetime as dt
import hashlib
import json
import pytest


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


def test_submitter_cannot_vote_and_official_cannot_override(client):
    id_=create(client)
    assert vote(client,id_,'org1').status_code==400
    assert vote(client,id_).status_code==200
    assert client.post(f'/api/reports/{id_}/finalize',headers=headers(client,role='official')).status_code==400
    assert client.post(f'/api/reports/{id_}/finalize',headers=headers(client,'org2')).status_code==403


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
                      (f'/api/reports/{id_}/expire',{}),
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


def test_corrections_and_interventions(client):
    id_=create(client);vote(client,id_);vote(client,id_,'org3')
    r=client.post(f'/api/reports/{id_}/corrections',json={'msg_id':'correction','label':'0','confidence':.9,'model_version':'test-v1','content':'Corrected claim','source_platform':'test'},headers=headers(client))
    assert r.status_code==200,r.text
    corrected=r.json()['id'];vote(client,corrected,'org2','0');vote(client,corrected,'org3','0')
    old=client.get(f'/api/reports/{id_}/chain',headers=headers(client)).json()
    assert old['status']=='SUPERSEDED' and old['final_label']=='1' and old['superseded_by']==corrected
    r=client.post(f'/api/reports/{corrected}/interventions',json={'intervention_id':'notice','action':'Publish correction','outcome':'Recorded','support':['notice']},headers=headers(client,'org2'))
    assert r.status_code==200,r.text
    assert client.get(f'/api/reports/{corrected}/verify',headers=headers(client)).json()['verified']


def test_expiry_and_late_vote(client):
    now=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc);client.ref.clock=lambda:now
    id_=create(client);vote(client,id_)
    client.ref.clock=lambda:now+dt.timedelta(hours=72)
    assert vote(client,id_,'org3').status_code==400
    assert client.post(f'/api/reports/{id_}/expire',headers=headers(client,role='official')).status_code==200


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
