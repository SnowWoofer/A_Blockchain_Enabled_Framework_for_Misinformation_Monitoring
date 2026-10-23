import { useState } from 'react';
import { Link } from 'react-router-dom';
import { submitTip } from '../../api/client';
import { CopyText, ErrorBox, Notice } from '../../components/ui';
import { rememberTip } from './TrackTip';

const MIN = 10;
const MAX = 2000;

export default function SubmitTip() {
  const [claim, setClaim] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const [reference, setReference] = useState<string>();
  const length = claim.trim().length;

  if (reference) {
    return (
      <div className="page narrow">
        <h1>Thank you — your report is in</h1>
        <Notice tone="success">
          The claim has been queued for AI screening. If it is flagged, member organisations will review it.
        </Notice>
        <p>Keep this reference to follow its progress. It is the only link between you and the report.</p>
        <p className="reference"><CopyText value={reference} /></p>
        <div className="actions">
          <Link className="btn btn-primary" to={`/track/${encodeURIComponent(reference)}`}>Track this report</Link>
          <button type="button" className="btn btn-ghost" onClick={() => { setReference(undefined); setClaim(''); }}>
            Report another claim
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="page narrow">
      <h1>Report a suspicious claim</h1>
      <p className="lead">Seen something on social media, in a group chat or on the radio that you think is false? Send it to the consortium.</p>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setError(undefined);
          submitTip(claim)
            .then((r) => { rememberTip(r.reference); setReference(r.reference); })
            .catch((err: Error) => setError(err))
            .finally(() => setBusy(false));
        }}
      >
        <label htmlFor="tip">The claim, as you saw it</label>
        <textarea id="tip" rows={7} value={claim} maxLength={MAX} required
          onChange={(e) => setClaim(e.target.value)} aria-describedby="tip-help tip-count"
          placeholder="e.g. “Voting stations in Polokwane will close at noon on election day.”" />
        <div className="field-foot">
          <span id="tip-help" className="muted small">Any language is fine. Please do not include names, phone numbers or other personal details.</span>
          <span id="tip-count" className={`small ${length > 0 && length < MIN ? 'text-warn' : 'muted'}`}>{length}/{MAX}</span>
        </div>
        {error && <ErrorBox error={error} />}
        <button type="submit" className="btn btn-primary" disabled={busy || length < MIN}>
          {busy ? 'Sending…' : 'Send for review'}
        </button>
        <p className="muted small">
          Reports are anonymous: no account is needed, and your IP address is used only briefly, in memory, to
          rate-limit submissions. It is never stored with the report or written to logs.
        </p>
      </form>
    </div>
  );
}
