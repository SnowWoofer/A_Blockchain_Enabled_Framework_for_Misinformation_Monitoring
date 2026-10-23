import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api/client';
import type { TipStatus } from '../../api/types';
import { ErrorBox, Loading, Notice, VerdictBadge } from '../../components/ui';

const STORE_KEY = 'mm.tips';

// Remembered only in this browser, purely so a reporter can find their
// references again; nothing depends on it surviving.
export function rememberTip(reference: string) {
  try {
    const list = JSON.parse(localStorage.getItem(STORE_KEY) ?? '[]') as string[];
    localStorage.setItem(STORE_KEY, JSON.stringify([reference, ...list.filter((r) => r !== reference)].slice(0, 10)));
  } catch {
    /* storage unavailable: fine */
  }
}

function rememberedTips(): string[] {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) ?? '[]') as string[];
  } catch {
    return [];
  }
}

function StatusView({ status }: { status: TipStatus }) {
  if (status.state === 'screening') {
    return (
      <Notice tone="info">
        <strong>Received.</strong> The claim is in the screening queue. If the AI model does not flag it, it may not
        go on to full review. Screening normally takes a few minutes.
      </Notice>
    );
  }
  if (status.state === 'under_review') {
    return (
      <Notice tone="info">
        <strong>Under review.</strong> Member organisations are investigating this claim. Reviews close after at
        most 72 hours.
      </Notice>
    );
  }
  return (
    <div className="panel">
      <p>Review is complete. The consortium's verdict:</p>
      {status.verdict && <VerdictBadge verdict={status.verdict} large />}
      <p><Link className="btn btn-primary" to={`/verdicts/${encodeURIComponent(status.report_id ?? '')}`}>Read the verdict</Link></p>
    </div>
  );
}

export default function TrackTip() {
  const { ref = '' } = useParams();
  const navigate = useNavigate();
  const [draft, setDraft] = useState(ref);
  const [state, setState] = useState<{ loading?: boolean; status?: TipStatus; error?: Error }>({});
  useEffect(() => {
    setDraft(ref);
    if (!ref) { setState({}); return; }
    setState({ loading: true });
    api<TipStatus>(`/api/public/tips/${encodeURIComponent(ref)}`)
      .then((status) => setState({ status }))
      .catch((error: Error) => setState({ error }));
  }, [ref]);
  const mine = rememberedTips();

  return (
    <div className="page narrow">
      <h1>Track a report</h1>
      <form className="search" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) navigate(`/track/${encodeURIComponent(draft.trim())}`); }}>
        <label htmlFor="ref" className="sr-only">Report reference</label>
        <input id="ref" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="tip_…" maxLength={128} spellCheck={false} />
        <button type="submit" className="btn btn-primary">Check</button>
      </form>
      {state.loading && <Loading />}
      {state.error && <ErrorBox error={state.error} />}
      {state.status && <StatusView status={state.status} />}
      {!ref && mine.length > 0 && (
        <section>
          <h2 className="h3">Reports sent from this browser</h2>
          <ul className="plain-list">
            {mine.map((r) => <li key={r}><Link to={`/track/${encodeURIComponent(r)}`}><code>{r}</code></Link></li>)}
          </ul>
        </section>
      )}
    </div>
  );
}
