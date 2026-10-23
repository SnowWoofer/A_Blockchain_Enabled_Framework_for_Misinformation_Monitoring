import { useState } from 'react';
import { ApiError, api } from '../../api/client';
import type { Admission, RegisteredOrg } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { ConsoleNav } from '../../components/Layout';
import { ErrorBox, Loading, Notice } from '../../components/ui';
import { formatDate, orgName } from '../../lib/format';
import { useResource } from '../../lib/useResource';

export default function Consortium() {
  const { me } = useAuth();
  const orgs = useResource(() => api<RegisteredOrg[]>('/api/orgs'), []);
  const [candidate, setCandidate] = useState('');
  const [admission, setAdmission] = useState<Admission | null>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const n = orgs.data?.length ?? 0;
  const needed = Math.ceil((2 * n) / 3);

  const lookup = (msp: string) => {
    setBusy(true);
    setError(undefined);
    api<Admission>(`/api/orgs/${encodeURIComponent(msp)}/admission`)
      .then(setAdmission)
      .catch((err: unknown) => {
        if (err instanceof ApiError && /no admission request/.test(err.message)) setAdmission(null);
        else setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setBusy(false));
  };

  const act = (path: string, body: unknown, message: string) => {
    if (!admission) return;
    setBusy(true);
    setError(undefined);
    api(`/api/orgs/${encodeURIComponent(admission.candidate_msp)}/admission${path}`, { method: 'POST', body })
      .then(() => { setNotice(message); lookup(admission.candidate_msp); orgs.reload(); })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const admits = admission?.votes.filter((v) => v.verdict === '1').length ?? 0;
  const myVote = admission?.votes.find((v) => v.voter_msp === me?.mspid);

  return (
    <div className="page">
      <h1>Consortium</h1>
      <ConsoleNav />
      <div className="two-col">
        <section className="panel">
          <h2 className="panel-title">Member organisations</h2>
          {orgs.loading && <Loading />}
          {orgs.error && <ErrorBox error={orgs.error} onRetry={orgs.reload} />}
          <ul className="plain-list">
            {orgs.data?.map((o) => (
              <li key={o.mspid} className="member">
                <strong>{orgName(o.mspid)}</strong> <span className="muted small">{o.mspid} · since {formatDate(o.registered_at)}</span>
                {o.mspid === me?.mspid && <span className="badge status-final">You</span>}
              </li>
            ))}
          </ul>
          <p className="muted small">Admitting a new member needs {needed} of {n} members to vote in favour.</p>
        </section>

        <section className="panel">
          <h2 className="panel-title">Admission requests</h2>
          <p className="muted small">A candidate organisation files its request with its own identity once its peer has joined the channel. Look it up by MSP ID to vote.</p>
          <form className="search" onSubmit={(e) => { e.preventDefault(); if (candidate.trim()) lookup(candidate.trim()); }}>
            <label htmlFor="cand" className="sr-only">Candidate MSP ID</label>
            <input id="cand" value={candidate} onChange={(e) => setCandidate(e.target.value)} placeholder="e.g. Org4MSP" maxLength={64} spellCheck={false} />
            <button type="submit" className="btn btn-secondary" disabled={busy}>Look up</button>
          </form>
          {notice && <Notice tone="success">{notice}</Notice>}
          {error && <ErrorBox error={error} />}
          {admission === null && <p className="muted">No admission request on the ledger for that MSP ID.</p>}
          {admission && (
            <div className="admission">
              <dl className="kv">
                <dt>Candidate</dt><dd>{admission.org_name} ({admission.candidate_msp})</dd>
                <dt>Requested</dt><dd>{formatDate(admission.requested_at)}</dd>
                <dt>Status</dt><dd>{admission.status}</dd>
                <dt>Votes to admit</dt><dd>{admits} of {needed} needed ({admission.votes.length} cast)</dd>
              </dl>
              {admission.status === 'PENDING' && (
                <div className="actions">
                  {myVote ? (
                    <span className="muted">Your organisation voted to {myVote.verdict === '1' ? 'admit' : 'reject'}.</span>
                  ) : (
                    <>
                      <button type="button" className="btn btn-primary btn-sm" disabled={busy}
                        onClick={() => act('/vote', { verdict: 'admit' }, 'Vote to admit recorded.')}>Vote to admit</button>
                      <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                        onClick={() => act('/vote', { verdict: 'reject' }, 'Vote to reject recorded.')}>Vote to reject</button>
                    </>
                  )}
                  <button type="button" className="btn btn-secondary btn-sm" disabled={busy || admits < needed}
                    onClick={() => act('/finalize', undefined, 'Admission finalised.')}>Finalise admission</button>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
