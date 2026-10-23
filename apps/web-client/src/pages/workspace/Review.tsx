import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../../api/client';
import type { ClaimDetail, Label } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { IntegrityCheck, LedgerHistory } from '../../components/LedgerPanels';
import {
  ConfidenceBar, ConsensusMeter, CopyText, ErrorBox, EvidenceLinks, Loading, Modal, Notice, OutcomeBadge, StatusBadge,
} from '../../components/ui';
import {
  OUTCOME_LABEL, formatDate, isPast, isSafeHttpUrl, orgName, platformLabel, relativeTime, shortId,
} from '../../lib/format';
import { useResource } from '../../lib/useResource';

const MIN_REASONING = 20;
const MAX_REASONING = 5000;
const MAX_LINKS = 10;

function VerdictForm({ claim, onSubmitted }: { claim: ClaimDetail; onSubmitted: () => void }) {
  const { me } = useAuth();
  const [outcome, setOutcome] = useState<Label>();
  const [reasoning, setReasoning] = useState('');
  const [links, setLinks] = useState<string[]>(['']);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const cleanLinks = links.map((l) => l.trim()).filter(Boolean);
  const badLinks = cleanLinks.filter((l) => !isSafeHttpUrl(l));
  const reasoningOk = reasoning.trim().length >= MIN_REASONING;
  const ready = !!outcome && reasoningOk && badLinks.length === 0;

  const submit = () => {
    setBusy(true);
    setError(undefined);
    api(`/fc/claims/${encodeURIComponent(claim.id)}/fact-check`, {
      method: 'POST', body: { outcome, reasoning: reasoning.trim(), support: cleanLinks },
    })
      .then(() => { setConfirming(false); onSubmitted(); })
      .catch((err: Error) => { setError(err.message); setConfirming(false); })
      .finally(() => setBusy(false));
  };

  return (
    <section className="panel verdict-form">
      <h2 className="panel-title">Your organisation's verdict</h2>
      <p className="muted small">
        You are voting on behalf of <strong>{orgName(me?.mspid)}</strong>. Each organisation has one vote, and
        it cannot be changed once recorded on the ledger.
      </p>
      <form onSubmit={(e) => { e.preventDefault(); if (ready) setConfirming(true); }} className="form">
        <fieldset className="choice-group">
          <legend>Is this claim misinformation?</legend>
          {(['1', '0'] as Label[]).map((value) => (
            <label key={value} className={`choice choice-${value === '1' ? 'yes' : 'no'}${outcome === value ? ' choice-on' : ''}`}>
              <input type="radio" name="outcome" value={value} checked={outcome === value} onChange={() => setOutcome(value)} />
              <span className="choice-title">{OUTCOME_LABEL[value]}</span>
              <span className="choice-help small">
                {value === '1' ? 'False or misleading, and the evidence shows it.' : 'Accurate, or not shown to be false.'}
              </span>
            </label>
          ))}
        </fieldset>

        <label htmlFor="reasoning">Reasoning</label>
        <textarea id="reasoning" rows={6} value={reasoning} maxLength={MAX_REASONING}
          onChange={(e) => setReasoning(e.target.value)} aria-describedby="reasoning-help" required
          placeholder="What did you check, and what did you find? This is published with the verdict." />
        <div className="field-foot">
          <span id="reasoning-help" className="muted small">Published with the verdict, attributed to your organisation (not to you personally).</span>
          <span className={`small ${reasoning.length && !reasoningOk ? 'text-warn' : 'muted'}`}>{reasoning.trim().length}/{MAX_REASONING}</span>
        </div>

        <span className="label">Evidence links</span>
        {links.map((link, i) => (
          <div key={i} className="link-row">
            <input type="url" value={link} placeholder="https://…" aria-label={`Evidence link ${i + 1}`}
              aria-invalid={!!link.trim() && !isSafeHttpUrl(link.trim())}
              onChange={(e) => setLinks(links.map((l, j) => (j === i ? e.target.value : l)))} />
            {links.length > 1 && (
              <button type="button" className="btn btn-ghost btn-sm" aria-label={`Remove link ${i + 1}`}
                onClick={() => setLinks(links.filter((_, j) => j !== i))}>Remove</button>
            )}
          </div>
        ))}
        {badLinks.length > 0 && <p className="small text-warn">Links must start with https:// or http://.</p>}
        {links.length < MAX_LINKS && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setLinks([...links, ''])}>Add another link</button>
        )}

        {error && <ErrorBox error={error} />}
        <button type="submit" className="btn btn-primary" disabled={!ready || busy}>Review and submit</button>
      </form>

      <Modal
        open={confirming}
        title="Record this vote on the ledger?"
        onClose={() => setConfirming(false)}
        actions={(
          <>
            <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)} disabled={busy}>Go back</button>
            <button type="button" className="btn btn-primary" onClick={submit} disabled={busy}>{busy ? 'Recording…' : 'Record vote'}</button>
          </>
        )}
      >
        <p>{orgName(me?.mspid)} votes: {outcome && <OutcomeBadge outcome={outcome} />}</p>
        <p className="muted small">
          {cleanLinks.length} evidence {cleanLinks.length === 1 ? 'link' : 'links'}. This is your organisation's only
          vote on this claim and cannot be changed afterwards.
        </p>
      </Modal>
    </section>
  );
}

export default function Review() {
  const { id = '' } = useParams();
  const { hasRole } = useAuth();
  const [justVoted, setJustVoted] = useState(false);
  const { data: claim, error, loading, reload } = useResource(
    () => api<ClaimDetail>(`/fc/claims/${encodeURIComponent(id)}`), [id]);

  if (loading && !claim) return <div className="page"><Loading /></div>;
  if (error || !claim) return <div className="page"><ErrorBox error={error ?? 'Not found'} onRetry={reload} /></div>;

  const open = claim.status === 'PENDING' || claim.status === 'UNDER_REVIEW';
  const c = claim.consensus;
  const canVoteHere = claim.can_vote && hasRole('fact_checker', 'official');

  return (
    <div className="page">
      <Link to="/workspace" className="back">← Review queue</Link>
      <div className="review-head">
        <div>
          <h1 className="h2">Claim review</h1>
          <p className="muted small">
            Report <CopyText value={claim.id} display={shortId(claim.id)} /> · raised by {orgName(claim.submitted_by)} ·{' '}
            {platformLabel(claim.source_platform)} · {formatDate(claim.submitted_at)}
          </p>
        </div>
        <div className="review-status">
          <StatusBadge status={claim.status} />
          {open && (
            <span className={`small ${isPast(claim.fact_check_deadline) ? 'text-warn' : 'muted'}`}>
              {isPast(claim.fact_check_deadline) ? 'Window closed ' : 'Closes '}{relativeTime(claim.fact_check_deadline)}
            </span>
          )}
        </div>
      </div>

      {justVoted && <Notice tone="success">Your organisation's vote is recorded on the ledger.</Notice>}

      <div className="two-col">
        <div>
          <section className="panel">
            <h2 className="panel-title">Claim</h2>
            <blockquote className="claim">{claim.content}</blockquote>
            <ConfidenceBar label={claim.inference_label} confidence={claim.confidence} />
            <p className="muted small">Screened by {claim.model_version}. The AI only prioritises claims; it does not decide them.</p>
          </section>

          {claim.my_org_is_submitter && (
            <Notice tone="info">Your organisation raised this claim, so it cannot vote on it (conflict of interest).</Notice>
          )}
          {claim.my_org_vote && (
            <Notice tone="info">Your organisation voted: <OutcomeBadge outcome={claim.my_org_vote} /></Notice>
          )}
          {canVoteHere && <VerdictForm claim={claim} onSubmitted={() => { setJustVoted(true); reload(); }} />}
          {claim.can_vote && !canVoteHere && <Notice tone="info">Observers can read claims but not vote.</Notice>}

          <section className="panel">
            <h2 className="panel-title">Other organisations' assessments</h2>
            {claim.blind ? (
              <p className="muted">
                Hidden until your organisation has voted, so that each assessment is independent. {c.votes_cast} of{' '}
                {c.eligible_voters} eligible organisations have voted so far.
              </p>
            ) : claim.fact_checks.length === 0 ? (
              <p className="muted">No votes yet.</p>
            ) : (
              <ul className="factchecks">
                {claim.fact_checks.map((fc) => (
                  <li key={fc.tx_id} className="factcheck">
                    <div className="factcheck-head"><strong>{orgName(fc.checker_msp)}</strong><OutcomeBadge outcome={fc.outcome} /></div>
                    {fc.reasoning && <p className="reasoning">{fc.reasoning}</p>}
                    <EvidenceLinks links={fc.support} />
                    <p className="muted small">{formatDate(fc.timestamp)} · tx {shortId(fc.tx_id, 10)}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <aside>
          <section className="panel">
            <h2 className="panel-title">Consensus</h2>
            <ConsensusMeter blind={claim.blind} yes={c.yes} no={c.no} cast={c.votes_cast}
              eligible={c.eligible_voters} required={c.required_yes} />
            <p className="muted small">
              {c.required_yes} of {c.registered_orgs} member organisations must find this claim to be misinformation to
              confirm it. Once that becomes impossible, the claim closes as not confirmed.
            </p>
          </section>
          <IntegrityCheck reportId={claim.id} />
          <LedgerHistory reportId={claim.id} />
        </aside>
      </div>
    </div>
  );
}
