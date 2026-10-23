import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { AuditEntry, AuditVerification } from '../../api/types';
import { ConsoleNav } from '../../components/Layout';
import { ErrorBox, Loading, Notice } from '../../components/ui';
import { formatDate, shortId } from '../../lib/format';
import { useResource } from '../../lib/useResource';

const ACTIONS = ['', 'login', 'login_failed', 'account_locked', 'access_denied', 'fact_check', 'report_submitted',
  'report_finalized', 'report_expired', 'user_created', 'user_updated', 'user_reset', 'password_changed',
  'mfa_enabled', 'mfa_disabled', 'refresh_token_reuse', 'admission_vote', 'api_key_issued'];

export default function Audit() {
  const [action, setAction] = useState('');
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [more, setMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error>();
  const integrity = useResource(() => api<AuditVerification>('/api/audit/verify'), []);

  const load = (beforeId?: number) => {
    setLoading(true);
    setError(undefined);
    api<AuditEntry[]>('/api/audit', { query: { action, before_id: beforeId, limit: 50 } })
      .then((page) => {
        setEntries((prev) => (beforeId ? [...prev, ...page] : page));
        setMore(page.length === 50);
      })
      .catch((err: Error) => setError(err))
      .finally(() => setLoading(false));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => load(), [action]);

  return (
    <div className="page">
      <h1>Audit log</h1>
      <ConsoleNav />
      {integrity.data && (integrity.data.intact
        ? <Notice tone="success">Hash chain intact across all {integrity.data.entries} entries (head {shortId(integrity.data.head ?? '', 12)}).</Notice>
        : <Notice tone="error">The audit log has been altered: the hash chain breaks at entry #{integrity.data.first_bad_id}.</Notice>)}
      <p className="muted small">Security-relevant events for your organisation. Each entry is chained to the previous one by hash, so editing or deleting history is detectable.</p>
      <div className="toolbar">
        <label>Event
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            {ACTIONS.map((a) => <option key={a} value={a}>{a ? a.replace(/_/g, ' ') : 'All events'}</option>)}
          </select>
        </label>
      </div>
      {error && <ErrorBox error={error} />}
      <div className="table-wrap">
        <table className="table table-dense">
          <thead><tr><th>Time</th><th>Who</th><th>Event</th><th>Target</th><th>Result</th><th>From</th></tr></thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} className={e.outcome !== 'ok' ? 'row-warn' : ''}>
                <td className="small">{formatDate(e.ts)}</td>
                <td>{e.actor}<div className="muted small">{e.role}</div></td>
                <td>{e.action.replace(/_/g, ' ')}</td>
                <td className="small"><code>{shortId(e.target, 14)}</code></td>
                <td>{e.outcome}</td>
                <td className="small muted">{e.ip}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {loading && <Loading />}
      {!loading && entries.length === 0 && <p className="muted">No events recorded.</p>}
      {more && entries.length > 0 && !loading && (
        <button type="button" className="btn btn-ghost" onClick={() => load(entries[entries.length - 1].id)}>Load older events</button>
      )}
    </div>
  );
}
