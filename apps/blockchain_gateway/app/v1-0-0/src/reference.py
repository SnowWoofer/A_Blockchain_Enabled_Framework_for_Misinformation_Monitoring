"""Centralised SQLite reference backend for controlled workflow comparisons.

This is an application audit log under one administrator, not an independently
endorsed ledger. Its threat assumptions are documented in info/ArtyIndo/repair/RESEARCH.md.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import math
import sqlite3
import threading
import uuid
from ledger_v3 import LedgerError

UTC=dt.timezone.utc


class ReferenceLedger:
    def __init__(self,path,orgs,clock=None):
        self.conn=sqlite3.connect(path,check_same_thread=False,timeout=30)
        self.lock=threading.RLock()
        self.clock=clock or (lambda:dt.datetime.now(UTC))
        self.conn.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY,v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history(seq INTEGER PRIMARY KEY,k TEXT,v TEXT,tx TEXT,ts TEXT);
            CREATE INDEX IF NOT EXISTS report_status ON state(json_extract(v,'$.status'),k);
        ''')
        with self.conn:
            # Configuration seeds a new reference database once. Changing an
            # environment variable must not bypass membership admission later.
            initialised=self.conn.execute("SELECT 1 FROM state LIMIT 1").fetchone()
            for org in ([] if initialised else orgs):
                key='org:'+f'Org{org.removeprefix("org")}MSP'
                self.conn.execute('INSERT OR IGNORE INTO state VALUES(?,?)',(key,json.dumps({'mspid':key[4:],'registered_at':self.clock().isoformat()})))

    def bridge(self,identity):
        return ReferenceBridge(self,identity)

    def get(self,k):
        row=self.conn.execute('SELECT v FROM state WHERE k=?',(k,)).fetchone()
        if row is None:raise LedgerError('record not found')
        return json.loads(row[0])

    def rows(self,prefix):
        return [json.loads(r[0]) for r in self.conn.execute('SELECT v FROM state WHERE k>=? AND k<? ORDER BY k',(prefix,prefix+'\uffff'))]

    def put(self,k,v,tx,ts):
        data=json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)
        self.conn.execute('INSERT OR REPLACE INTO state VALUES(?,?)',(k,data))
        self.conn.execute('INSERT INTO history(k,v,tx,ts) VALUES(?,?,?,?)',(k,data,tx,ts))


class ReferenceBridge:
    def __init__(self,ledger,identity):self.ledger,self.identity=ledger,identity

    def call(self,fn,*args,query=False):
        l=self.ledger
        with l.lock:
            try:
                # BEGIN IMMEDIATE prevents read/modify/write races between clients.
                l.conn.execute('BEGIN' if query else 'BEGIN IMMEDIATE')
                result=self._call(fn,list(args),query)
                l.conn.commit()
                return result
            except Exception:
                l.conn.rollback()
                raise

    def _call(self,fn,a,query):
        l=self.ledger; msp=self.identity.msp;role=self.identity.role;now=l.clock();ts=now.isoformat();tx=uuid.uuid4().hex
        reads={'QueryReport','QueryAllReports','QueryReportsPage','QueryReportHistory','QueryInterventions','QueryReportNotes','ListRegisteredOrgs','QueryOrgAdmission'}
        if query != (fn in reads):raise LedgerError('incorrect operation type')
        if fn=='ListRegisteredOrgs':return l.rows('org:')
        if fn=='QueryReport':return l.get('report:'+a[0])
        if fn=='QueryAllReports':return l.rows('report:')
        if fn=='QueryReportsPage':
            status,size,bookmark=a[0],int(a[1]),a[2]
            if not 1<=size<=200:raise LedgerError('page_size must be 1..200')
            sql='SELECT v FROM state WHERE k>=? AND k<? AND k>?'
            params=['report:','report:\uffff','report:'+bookmark]
            if status:sql+=" AND json_extract(v,'$.status')=?";params.append(status)
            sql+=' ORDER BY k LIMIT ?';params.append(size+1)
            rows=[json.loads(r[0]) for r in l.conn.execute(sql,params)]
            return {'items':rows[:size],'next_bookmark':rows[size-1]['id'] if len(rows)>size else ''}
        if fn=='QueryReportHistory':
            l.get('report:'+a[0])
            return [{'tx_id':txid,'timestamp':time,'is_delete':False,'record':json.loads(data)} for data,txid,time in l.conn.execute('SELECT v,tx,ts FROM history WHERE k=? ORDER BY seq',('report:'+a[0],))]
        if fn=='QueryInterventions':l.get('report:'+a[0]);return l.rows('intervention:'+a[0]+':')
        if fn=='QueryReportNotes':l.get('report:'+a[0]);return l.rows('report_note:'+a[0]+':')
        if fn=='QueryOrgAdmission':return l.get('admission:'+a[0])
        if role not in ('fact_checker','official'):raise LedgerError('role is not permitted')
        if fn=='RequestOrgAdmission':
            if role!='official':raise LedgerError('official required')
            try:l.get('org:'+msp)
            except LedgerError:pass
            else:raise LedgerError('organisation already registered')
            try:old=l.get('admission:'+msp)
            except LedgerError:old=None
            if old and old['status']=='PENDING':raise LedgerError('pending admission already exists')
            if not a[0].strip():raise LedgerError('org_name required')
            l.put('admission:'+msp,{'candidate_msp':msp,'org_name':a[0],'org_type':a[1],'status':'PENDING','votes':[],'requested_at':ts},tx,ts);return msp
        l.get('org:'+msp)
        if fn in ('VoteOnOrgAdmission','FinalizeOrgAdmission'):
            if role!='official':raise LedgerError('official required')
            r=l.get('admission:'+a[0])
            if r['status']!='PENDING':raise LedgerError('admission is closed')
            if fn=='VoteOnOrgAdmission':
                if a[1] not in ('0','1') or a[0]==msp:raise LedgerError('invalid admission vote')
                if any(v['voter_msp']==msp for v in r['votes']):raise LedgerError('duplicate admission vote')
                r['votes'].append({'voter_msp':msp,'verdict':a[1],'txid':tx})
            else:
                n=len(l.rows('org:'));needed=(2*n+2)//3
                if sum(v['verdict']=='1' for v in r['votes'])<needed:raise LedgerError('admission quorum not reached')
                r.update(status='ADMITTED',finalized_by=msp,finalized_at=ts)
                l.put('org:'+a[0],{'mspid':a[0],'registered_at':ts},tx,ts)
            l.put('admission:'+a[0],r,tx,ts);return None
        if fn in ('SubmitReport','Submit'):
            if fn=='SubmitReport':s=json.loads(a[0])
            else:s=dict(zip(('id','content_hash','label','confidence','model_version','timestamp'),a));s['confidence']=float(s['confidence'])
            if not s.get('id') or s.get('label') not in ('0','1') or not s.get('model_version'):raise LedgerError('invalid report fields')
            if len(s.get('content_hash',''))!=64:raise LedgerError('invalid SHA-256')
            try:int(s['content_hash'],16);dt.datetime.fromisoformat(s['timestamp'].replace('Z','+00:00'))
            except (ValueError,KeyError):raise LedgerError('invalid report fields')
            if not math.isfinite(float(s['confidence'])) or not 0<=float(s['confidence'])<=1:raise LedgerError('invalid confidence')
            if fn=='SubmitReport' and not s.get('claim_cid'):raise LedgerError('claim_cid required')
            try:prev=l.get('report:'+s['id'])
            except LedgerError:prev=None
            if prev:
                same=prev['submitted_by']==msp and all(prev.get(k,'')==s.get(v,'') for k,v in [('content_hash','content_hash'),('inference_label','label'),('confidence','confidence'),('model_version','model_version'),('claim_cid','claim_cid'),('supersedes','supersedes')])
                if same:return None
                raise LedgerError('conflicting reuse of report id')
            eligible=sorted(r['mspid'] for r in l.rows('org:') if r['mspid']!=msp)
            if len(eligible)<2:raise LedgerError('at least two independent reviewers required')
            parent=s.get('supersedes','')
            if parent:raise LedgerError('supersession is retired; append a report note instead')
            r={'schema_version':3,'id':s['id'],'content_hash':s['content_hash'],'claim_cid':s.get('claim_cid',''),
               'inference_label':s['label'],'confidence':s['confidence'],'model_version':s['model_version'],
               'timestamp':ts,'submitted_by':msp,'fact_check_deadline':(now+dt.timedelta(hours=72)).isoformat(),
               'status':'PENDING','fact_checks':[],'eligible_reviewers':eligible,'required_votes':len(eligible)//2+1,'supersedes':parent}
            l.put('report:'+s['id'],r,tx,ts);return None
        r=l.get('report:'+a[0])
        if fn=='RecordReportNote':
            if r['submitted_by']!=msp:raise LedgerError('only the submitting organisation may append notes')
            _,id_,cid,digest=a
            if not id_.strip() or len(id_)>256 or not cid.strip() or len(digest)!=64:raise LedgerError('invalid note')
            try:int(digest,16)
            except ValueError:raise LedgerError('invalid digest')
            key=f'report_note:{a[0]}:{msp}:{id_}'
            try:old=l.get(key)
            except LedgerError:old=None
            if old:
                if old['evidence_cid']==cid and old['evidence_hash']==digest:return None
                raise LedgerError('conflicting note id',409)
            l.put(key,{'id':id_,'report_id':a[0],'organisation':msp,'evidence_cid':cid,'evidence_hash':digest,'timestamp':ts,'txid':tx},tx,ts);return None
        if fn=='ExpireReport' and role!='official':raise LedgerError('official required')
        if fn=='ExpireReport' and r['status']=='EXPIRED':return None
        if fn=='SubmitFactCheck':
            if len(a)!=4 or a[1] not in ('0','1') or not a[2] or len(a[3])!=64:raise LedgerError('invalid evidence commitment')
            try:int(a[3],16)
            except ValueError:raise LedgerError('invalid digest')
            for v in r['fact_checks']:
                if v['checker_msp']==msp:
                    if (v['outcome'],v['evidence_cid'],v['evidence_hash'])==tuple(a[1:]):return None
                    raise LedgerError('organisation already voted')
        if r['status'] not in ('PENDING','UNDER_REVIEW'):raise LedgerError('report is '+r['status'])
        late=now>=dt.datetime.fromisoformat(r['fact_check_deadline'])
        if fn=='ExpireReport':
            if not late:raise LedgerError('still within fact-check window')
            r.update(status='EXPIRED',finalized_by=msp,finalized_at=ts)
        else:
            if late:raise LedgerError('fact-check deadline has passed')
            if fn=='SubmitFactCheck':
                if msp not in r['eligible_reviewers']:raise LedgerError('not an eligible independent reviewer')
                r['fact_checks'].append({'checker_msp':msp,'outcome':a[1],'evidence_cid':a[2],'evidence_hash':a[3],'timestamp':ts,'txid':tx});r['status']='UNDER_REVIEW'
            else:raise LedgerError('unsupported operation')
            for label in ('0','1'):
                if sum(v['outcome']==label for v in r['fact_checks'])>=r['required_votes']:
                    r.update(status='FINAL',final_label=label,finalized_by=msp,finalized_at=ts);break
            if r['status']!='FINAL' and len(r['fact_checks'])==len(r['eligible_reviewers']):r.update(status='DISPUTED',finalized_by=msp,finalized_at=ts)
        l.put('report:'+r['id'],r,tx,ts)
        return None
