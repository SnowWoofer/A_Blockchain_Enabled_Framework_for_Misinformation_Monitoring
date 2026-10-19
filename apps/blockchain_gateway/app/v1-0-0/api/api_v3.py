"""Version 3 workflow API: immutable claim/evidence objects, strict roles."""
from __future__ import annotations
import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from report import canonical_json, content_hash
from ledger_v3 import FabricLedger, LedgerError
from reference import ReferenceLedger
from auth_v3 import Identity, permit, require_org, set_server_store
from objects import OffChainStore, StorageError

STORE = None
REFERENCE = None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class ReportCreate(StrictModel):
    msg_id: str = Field(min_length=1,max_length=256)
    label: str = Field(pattern='^[01]$')
    confidence: float = Field(ge=0,le=1)
    model_version: str = Field(min_length=1,max_length=256)
    content: str = Field(min_length=1,max_length=200000)
    source_platform: str = Field(min_length=1,max_length=256)
    published_at: str = ''
    inference_timestamp: str = ''


class FactCheckSubmission(StrictModel):
    outcome: str = Field(pattern='^[01]$')
    reasoning: str = Field(min_length=1,max_length=100000)
    support: list[str] = Field(default_factory=list,max_length=100)


class NoteSubmission(StrictModel):
    note_id: str = Field(min_length=1,max_length=256)
    text: str = Field(min_length=1,max_length=100000)
    support: list[str] = Field(default_factory=list,max_length=100)


class AdmissionRequest(StrictModel):
    org_name: str = Field(min_length=1,max_length=256)
    org_type: str = Field(min_length=1,max_length=128)


class AdmissionVote(StrictModel):
    verdict: str = Field(pattern='^[01]$')


def bridge_for(identity):
    return REFERENCE.bridge(identity) if REFERENCE is not None else FabricLedger(identity)


def ledger(identity,fn,*args,query=False):
    try:
        return bridge_for(identity).call(fn,*args,query=query)
    except LedgerError as exc:
        raise HTTPException(exc.status_code,str(exc)) from exc


def object_get(cid):
    if not cid: raise HTTPException(409,'record has no content commitment')
    try:return STORE.get_object(cid)
    except StorageError as exc:raise HTTPException(503,'committed content unavailable') from exc


def object_put(payload,kind):
    try:return STORE.put_object(payload,kind)
    except StorageError as exc:raise HTTPException(503,'required content storage unavailable') from exc


async def expiry_loop(identity,interval=60):
    while True:
        try:
            await asyncio.to_thread(expiry_pass,identity)
        except Exception:
            # Details can contain service configuration; do not log credentials.
            print('[expiry] pass failed; will retry')
        await asyncio.sleep(interval)


def expiry_pass(identity):
    permit(identity,'official')
    now=dt.datetime.now(dt.timezone.utc)
    # Collect before mutating the status index so page bookmarks remain stable.
    overdue=[]
    for status in ('PENDING','UNDER_REVIEW'):
        bookmark=''
        while True:
            page=ledger(identity,'QueryReportsPage',status,50,bookmark,query=True)
            overdue.extend(r['id'] for r in page['items'] if dt.datetime.fromisoformat(r['fact_check_deadline'].replace('Z','+00:00'))<=now)
            bookmark=page['next_bookmark']
            if not bookmark:break
    for report_id in overdue:
        try:ledger(identity,'ExpireReport',report_id)
        except HTTPException:continue # a concurrent finalisation may have won


@contextlib.asynccontextmanager
async def lifespan(app):
    global STORE,REFERENCE
    STORE=OffChainStore(os.environ.get('OFFCHAIN_DB','/tmp/misinfo-offchain-v3.db'))
    set_server_store(STORE)
    credentials_path=os.environ.get('CREDENTIALS_FILE')
    if not credentials_path:raise RuntimeError('CREDENTIALS_FILE is required; provision credentials before startup')
    credentials=json.loads(Path(credentials_path).read_text())
    for row in credentials:
        if 'digest' in row:
            STORE.provision_digest(row['digest'],row['org'],row['role'])
            if row.get('revoked'):STORE.revoke_digest(row['digest'])
        elif os.environ.get('LEDGER_BACKEND')=='sqlite-reference':STORE.provision_key(row['api_key'],row['org'],row['role'])
        else:raise RuntimeError('API registry must contain credential digests, not raw keys')
    if os.environ.get('LEDGER_BACKEND','fabric')=='sqlite-reference':
        REFERENCE=ReferenceLedger(os.environ.get('REFERENCE_DB','/tmp/misinfo-reference-v3.db'),
            os.environ.get('ORGS','org1,org2,org3').split(','))
    elif os.environ.get('LEDGER_BACKEND','fabric')!='fabric':raise RuntimeError('unknown ledger backend')
    task=None
    if os.environ.get('ENABLE_EXPIRY','false').lower()=='true':
        expiry_path=os.environ.get('EXPIRY_CREDENTIAL_FILE')
        row=json.loads(Path(expiry_path).read_text()) if expiry_path else None
        if row is None:raise RuntimeError('expiry requires a provisioned official credential')
        task=asyncio.create_task(expiry_loop(Identity(row['org'],row['role'],row['api_key'])))
    try:yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):await task
        STORE.close();set_server_store(None)
        if REFERENCE is not None:REFERENCE.conn.close();REFERENCE=None


app=FastAPI(title='Misinformation Monitoring',version='3.1.0',lifespan=lifespan)


@app.get('/health')
def health():return {'status':'ok' if STORE else 'starting','schema_version':3}


@app.get('/api/status')
def storage_status(identity:Identity=Depends(require_org)):
    return STORE.ipfs_status() | {'ledger_backend':'sqlite-reference' if REFERENCE else 'fabric'}


@app.get('/api/orgs')
def list_orgs(identity:Identity=Depends(require_org)):
    return ledger(identity,'ListRegisteredOrgs',query=True)


@app.post('/api/orgs/{msp}/admission')
def request_admission(msp:str,body:AdmissionRequest,identity:Identity=Depends(require_org)):
    permit(identity,'official')
    if msp!=identity.msp:raise HTTPException(403,'candidate must match authenticated organisation')
    return {'candidate_msp':ledger(identity,'RequestOrgAdmission',body.org_name,body.org_type)}


@app.post('/api/orgs/{msp}/admission/vote')
def vote_admission(msp:str,body:AdmissionVote,identity:Identity=Depends(require_org)):
    permit(identity,'official');ledger(identity,'VoteOnOrgAdmission',msp,body.verdict);return {'ok':True}


@app.post('/api/orgs/{msp}/admission/finalize')
def finalize_admission(msp:str,identity:Identity=Depends(require_org)):
    permit(identity,'official');ledger(identity,'FinalizeOrgAdmission',msp);return {'ok':True}


@app.get('/api/orgs/{msp}/admission')
def get_admission(msp:str,identity:Identity=Depends(require_org)):
    return ledger(identity,'QueryOrgAdmission',msp,query=True)


@app.post('/api/reports')
def create_report(body:ReportCreate,identity:Identity=Depends(require_org)):
    permit(identity,'official','fact_checker')
    if not body.content.strip():raise HTTPException(422,'content must not be blank')
    # Inference time can change after Kafka replay; it is preserved in the first
    # document, but does not make an otherwise identical request a conflict.
    # Retain the empty legacy field in sealed objects/fingerprints so existing
    # ordinary submissions remain retryable. New supersession is not accepted.
    fields=body.model_dump() | {'supersedes':''}
    fingerprint=content_hash({k:v for k,v in fields.items() if k!='inference_timestamp'})
    report_id=hashlib.sha256((identity.msp+'\0'+body.msg_id).encode()).hexdigest()
    previous=STORE.existing_submission(identity.org,body.msg_id)
    if previous is None:
        # The index can be rebuilt after a local database loss. The ledger is
        # authoritative; never manufacture a new CID for an existing message.
        try:
            committed=ledger(identity,'QueryReport',report_id,query=True)
        except HTTPException as exc:
            if not any(s in str(exc.detail).lower() for s in ('not found','does not exist','no report found')):
                raise
        else:
            old=object_get(committed.get('claim_cid'))
            recovered={'msg_id':old['msg_id'],'label':old['inference']['label'],
                'confidence':old['inference']['confidence'],'model_version':old['inference']['model_version'],
                'content':old['source']['content'],'source_platform':old['source']['platform'],
                'published_at':old['source']['published_at'],'supersedes':old.get('supersedes','')}
            if content_hash(recovered)!=fingerprint:
                raise HTTPException(409,'msg_id reused with different committed content')
            previous=STORE.reserve_submission(identity.org,body.msg_id,fingerprint,report_id,committed['claim_cid'])
    if previous and previous['fingerprint']!=fingerprint:raise HTTPException(409,'msg_id reused with different content')
    if previous:
        cid=previous['cid'];claim=object_get(cid);digest=content_hash(claim)
    else:
        claim={'schema_version':3,'kind':'claim','id':report_id,'msg_id':body.msg_id,
            'source':{'platform':body.source_platform,'content':body.content,'published_at':body.published_at},
            'inference':{'label':body.label,'confidence':body.confidence,'model_version':body.model_version,'inference_timestamp':body.inference_timestamp},
            'submitter':{'org_mspid':identity.msp},'supersedes':''}
        cid,digest=object_put(claim,'claim')
        try:reservation=STORE.reserve_submission(identity.org,body.msg_id,fingerprint,report_id,cid)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        if reservation['cid']!=cid:
            cid=reservation['cid'];claim=object_get(cid);digest=content_hash(claim)
    sub={'id':report_id,'content_hash':digest,'claim_cid':cid,'label':body.label,
         'confidence':body.confidence,'model_version':body.model_version,
         'timestamp':dt.datetime.now(dt.timezone.utc).isoformat(),'supersedes':''}
    ledger(identity,'SubmitReport',canonical_json(sub));STORE.mark_committed(cid)
    return {'id':report_id,'content_hash':digest,'claim_cid':cid,'schema_version':3}


@app.get('/api/reports')
def list_reports(status:str='',page_size:int=Query(50,ge=1,le=200),bookmark:str='',identity:Identity=Depends(require_org)):
    return ledger(identity,'QueryReportsPage',status,page_size,bookmark,query=True)


@app.get('/api/reports/{report_id}/chain')
def get_chain(report_id:str,identity:Identity=Depends(require_org)):
    return ledger(identity,'QueryReport',report_id,query=True)


def legacy_interventions(report_id,identity):
    """Read historical content without restoring its retired write workflow."""
    result=[]
    for row in ledger(identity,'QueryInterventions',report_id,query=True):
        item=row | {'available':False,'verified':False,'content':None}
        try:
            doc=object_get(row['evidence_cid'])
            expected={'kind':'intervention','schema_version':3,'report_id':report_id,
                      'organisation':row['organisation'],'intervention_id':row['id']}
            valid=isinstance(doc,dict) and content_hash(doc)==row['evidence_hash'] and all(doc.get(k)==v for k,v in expected.items())
            item.update(available=True,verified=valid)
            if valid:item['content']=doc
        except HTTPException:pass
        result.append(item)
    return result


@app.get('/api/reports/{report_id}')
def get_report(report_id:str,identity:Identity=Depends(require_org)):
    rec=get_chain(report_id,identity);claim=object_get(rec.get('claim_cid'))
    if content_hash(claim)!=rec['content_hash']:
        raise HTTPException(409,'committed claim integrity check failed')
    checks=[]
    for vote in rec.get('fact_checks',[]):
        try:
            evidence=object_get(vote['evidence_cid'])
            if content_hash(evidence)!=vote['evidence_hash']:
                checks.append({'checker_msp':vote['checker_msp'],'outcome':vote['outcome'],'available':True,'verified':False})
            else:checks.append(evidence|{'txid':vote['txid'],'evidence_hash':vote['evidence_hash'],'verified':True})
        except HTTPException:checks.append({'checker_msp':vote['checker_msp'],'outcome':vote['outcome'],'available':False})
    return claim|{'report_id':report_id,'content_hash':rec['content_hash'],'fact_check_status':rec['status'],
                  'fact_checks':checks,'legacy_interventions':legacy_interventions(report_id,identity)}


@app.post('/api/reports/{report_id}/fact-check')
def submit_fact_check(report_id:str,body:FactCheckSubmission,identity:Identity=Depends(require_org)):
    permit(identity,'official','fact_checker')
    doc={'schema_version':3,'kind':'fact_check','report_id':report_id,'checker_msp':identity.msp,
         'outcome':body.outcome,'reasoning':body.reasoning,'support':body.support}
    cid,digest=object_put(doc,'fact_check')
    ledger(identity,'SubmitFactCheck',report_id,body.outcome,cid,digest);STORE.mark_committed(cid)
    return {'ok':True,'evidence_cid':cid,'evidence_hash':digest,'status':get_chain(report_id,identity)['status']}


@app.post('/api/reports/{report_id}/notes')
def append_note(report_id:str,body:NoteSubmission,identity:Identity=Depends(require_org)):
    permit(identity,'official','fact_checker')
    rec=get_chain(report_id,identity)
    if rec['submitted_by']!=identity.msp:raise HTTPException(403,'only the submitting organisation may append notes')
    if not body.note_id.strip() or not body.text.strip():raise HTTPException(422,'note_id and text must not be blank')
    doc={'schema_version':3,'kind':'report_note','report_id':report_id,'organisation':identity.msp,**body.model_dump()}
    digest=content_hash(doc)
    for row in ledger(identity,'QueryReportNotes',report_id,query=True):
        if row['id']==body.note_id:
            if row['evidence_hash']!=digest:raise HTTPException(409,'conflicting note id')
            STORE.mark_committed(row['evidence_cid'])
            return {'ok':True,'note_id':body.note_id,'evidence_cid':row['evidence_cid'],'evidence_hash':digest}
    cid,digest=object_put(doc,'report_note')
    try:ledger(identity,'RecordReportNote',report_id,body.note_id,cid,digest)
    except HTTPException as exc:
        if 'conflicting note id' in str(exc.detail):raise HTTPException(409,'conflicting note id') from exc
        raise
    STORE.mark_committed(cid)
    return {'ok':True,'note_id':body.note_id,'evidence_cid':cid,'evidence_hash':digest}


@app.get('/api/reports/{report_id}/notes')
def notes(report_id:str,identity:Identity=Depends(require_org)):
    rec=get_chain(report_id,identity)
    result=[]
    for row in ledger(identity,'QueryReportNotes',report_id,query=True):
        item=row | {'note_id':row['id'],'available':False,'verified':False,'text':None,'support':None}
        try:
            doc=object_get(row['evidence_cid'])
            expected={'kind':'report_note','schema_version':3,'report_id':report_id,
                      'organisation':row['organisation'],'note_id':row['id']}
            valid=isinstance(doc,dict) and content_hash(doc)==row['evidence_hash'] and all(doc.get(k)==v for k,v in expected.items())
            valid=valid and row['organisation']==rec['submitted_by']
            if valid:
                try:
                    parsed=NoteSubmission.model_validate({key:doc.get(key) for key in ('note_id','text','support')})
                    valid=bool(parsed.note_id.strip() and parsed.text.strip())
                except ValidationError:valid=False
            item.update(available=True,verified=valid)
            if valid:item.update(text=doc['text'],support=doc['support'])
        except HTTPException:pass
        result.append(item)
    return result


@app.get('/api/reports/{report_id}/history')
def history(report_id:str,identity:Identity=Depends(require_org)):
    rows=ledger(identity,'QueryReportHistory',report_id,query=True)
    # Fabric >=2 returns newest first in committed transaction order. The
    # reference store returns ascending sequence. Expose oldest first for both.
    return rows if REFERENCE is not None else list(reversed(rows))


@app.get('/api/reports/{report_id}/verify')
def verify_report(report_id:str,identity:Identity=Depends(require_org)):
    rec=get_chain(report_id,identity)
    if rec.get('schema_version')!=3:return {'verified':False,'schema_version':rec.get('schema_version',1),'reason':'legacy guarantees unavailable'}
    checks=[]
    def check(cid,digest,expected):
        try:
            doc=object_get(cid)
            valid=isinstance(doc,dict) and content_hash(doc)==digest and all(doc.get(k)==v for k,v in expected.items())
            return {'cid':cid,'available':True,'verified':valid}
        except HTTPException:return {'cid':cid,'available':False,'verified':False}
    claim=check(rec.get('claim_cid'),rec['content_hash'],{'id':report_id,'kind':'claim','schema_version':3,
        'submitter':{'org_mspid':rec['submitted_by']},'supersedes':rec.get('supersedes','')})
    if claim['verified']:
        doc=object_get(rec['claim_cid'])
        inf=doc.get('inference',{})
        claim['verified']=inf.get('label')==rec['inference_label'] and all(inf.get(k)==rec[k] for k in ('confidence','model_version'))
    for vote in rec['fact_checks']:
        checks.append(check(vote['evidence_cid'],vote['evidence_hash'],{'kind':'fact_check','report_id':report_id,'checker_msp':vote['checker_msp'],'outcome':vote['outcome'],'schema_version':3}))
    # Read legacy intervention commitments without permitting new writes.
    for row in ledger(identity,'QueryInterventions',report_id,query=True):
        checks.append(check(row['evidence_cid'],row['evidence_hash'],{'kind':'intervention','report_id':report_id,'organisation':row['organisation'],'intervention_id':row['id'],'schema_version':3}))
    note_checks=notes(report_id,identity)
    entries=history(report_id,identity)
    prior=set();history_ok=bool(entries)
    for entry in entries:
        state=entry.get('record') or {}
        immutable=('id','content_hash','claim_cid','submitted_by','eligible_reviewers','required_votes',
            'inference_label','confidence','model_version','timestamp','fact_check_deadline','supersedes','schema_version')
        if any(state.get(k)!=rec.get(k) for k in immutable):history_ok=False
        votes={(v['checker_msp'],v['outcome'],v.get('evidence_hash'),v.get('evidence_cid')) for v in state.get('fact_checks',[])}
        if not prior.issubset(votes):history_ok=False
        prior=votes
    current={(v['checker_msp'],v['outcome'],v['evidence_hash'],v['evidence_cid']) for v in rec['fact_checks']}
    history_ok=history_ok and prior==current
    if entries and entries[-1].get('record')!=rec:history_ok=False
    return {'report_id':report_id,'schema_version':3,'claim':claim,'evidence':checks,'notes':note_checks,'history_verified':history_ok,
        'verified':claim['verified'] and history_ok and all(c['verified'] for c in checks+note_checks),
        'meaning':'Integrity of committed objects and ledger history, not factual truth or legal compliance.'}
