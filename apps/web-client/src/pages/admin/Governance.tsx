import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { ChainRecord, RegisteredOrg } from '../../api/types';
import { ConsoleNav } from '../../components/Layout';
import { Empty, ErrorBox, Loading, Modal, Notice, Stat, StatusBadge } from '../../components/ui';
import { isPast, orgName, relativeTime, shortId } from '../../lib/format';
import { useResource } from '../../lib/useResource';

type Action = { kind: 'finalize' | 'expire'; record: ChainRecord };

function quorum(n: number) { return Math.ceil((2 * n) / 3); }

/** What an official may do with a report whose window has closed, mirroring the chaincode. */
function options(r: ChainRecord) {
  const yes = r.fact_checks.filter((f) => f.outcome === '1').length;
  const no = r.fact_checks.length - yes;
  return { yes, no, canFinalize: r.fact_checks.length >= 2 && yes !== no };
}

export default function Governance() {
  const reports = useResource(() => api<ChainRecord[]>('/api/reports'), []);
  const orgs = useResource(() => api<RegisteredOrg[]>('/api/orgs'), []);
  const [action, setAction] = useState<Action>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [done, setDone] = useState<string>();

  const { open, overdue, counts } = useMemo(() => {
    const all = reports.data ?? [];
    const openReports = all.filter((r) => r.status === 'PENDING' || r.status === 'UNDER_REVIEW');
    const tally: Record<string, number> = {};
    all.forEach((r) => { tally[r.status] = (tally[r.status] ?? 0) + 1; });
    return {
      open: openReports,
      overdue: openReports.filter((r) => isPast(r.fact_check_deadline))
        .sort((a, b) => a.fact_check_deadline.localeCompare(b.fact_check_deadline)),
      counts: tally,
    };
  }, [reports.data]);
  const n = orgs.data?.length ?? 0;

  const run = () => {
    if (!action) return;
    setBusy(true);
    setError(undefined);
    api(`/api/reports/${encodeURIComponent(action.record.id)}/${action.kind}`, { method: 'POST' })
      .then(() => {
        setDone(`${action.kind === 'finalize' ? 'Finalised' : 'Expired'} report ${shortId(action.record.id)}.`);
        setAction(undefined);
        reports.reload();
      })
      .catch((err: Error) => { setError(err.message); setAction(undefined); })
      .finally(() => setBusy(false));
  };

  return (
    <div className="page">
      <h1>Officials' console</h1>
      <ConsoleNav />
      {(reports.loading || orgs.loading) && <Loading />}
      {reports.error && <ErrorBox error={reports.error} onRetry={reports.reload} />}
      {done && <Notice tone="success">{done}</Notice>}
      {error && <ErrorBox error={error} />}

      <section className="stats">
        <Stat value={open.length} label="Open for review" />
        <Stat value={overdue.length} label="Window closed, awaiting decision" tone={overdue.length ? 'warn' : undefined} />
        <Stat value={counts.FINAL ?? 0} label="Final" />
        <Stat value={counts.REJECTED ?? 0} label="Not confirmed" />
        <Stat value={n ? `${quorum(n)} / ${n}` : '—'} label="Quorum (yes votes / members)" />
      </section>

      <section>
        <h2>Awaiting a decision</h2>
        <p className="muted">
          These reports reached the end of their 72-hour window without settling the two-thirds rule. An official may
          close each one by simple majority of the votes cast (at least two, no tie) or let it expire. Neither is
          possible while a window is still open.
        </p>
        {reports.data && overdue.length === 0 && <Empty title="No reports are waiting for a decision" />}
        <div className="table-wrap">
          {overdue.length > 0 && (
            <table className="table">
              <thead><tr><th>Report</th><th>Status</th><th>Votes (yes / no)</th><th>Window closed</th><th /></tr></thead>
              <tbody>
                {overdue.map((r) => {
                  const o = options(r);
                  return (
                    <tr key={r.id}>
                      <td><Link to={`/workspace/claims/${encodeURIComponent(r.id)}`}>{shortId(r.id)}</Link><div className="muted small">raised by {orgName(r.submitted_by)}</div></td>
                      <td><StatusBadge status={r.status} /></td>
                      <td>{o.yes} / {o.no}</td>
                      <td>{relativeTime(r.fact_check_deadline)}</td>
                      <td className="row-actions">
                        <button type="button" className="btn btn-secondary btn-sm" disabled={!o.canFinalize}
                          title={o.canFinalize ? '' : 'Needs at least two votes and no tie'}
                          onClick={() => setAction({ kind: 'finalize', record: r })}>Finalise by majority</button>
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAction({ kind: 'expire', record: r })}>Expire</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <Modal
        open={!!action}
        title={action?.kind === 'finalize' ? 'Finalise by majority?' : 'Expire without a verdict?'}
        onClose={() => setAction(undefined)}
        actions={(
          <>
            <button type="button" className="btn btn-ghost" onClick={() => setAction(undefined)} disabled={busy}>Cancel</button>
            <button type="button" className={`btn ${action?.kind === 'expire' ? 'btn-danger' : 'btn-primary'}`} onClick={run} disabled={busy}>
              {busy ? 'Recording…' : action?.kind === 'finalize' ? 'Finalise' : 'Expire'}
            </button>
          </>
        )}
      >
        {action && (action.kind === 'finalize'
          ? <p>Report {shortId(action.record.id)} will close as <strong>{options(action.record).yes > options(action.record).no ? 'misinformation' : 'not misinformation'}</strong>, the majority of the {action.record.fact_checks.length} votes cast. This is recorded on the ledger under your organisation and cannot be undone.</p>
          : <p>Report {shortId(action.record.id)} will close with no verdict. This is recorded on the ledger and cannot be undone.</p>)}
      </Modal>
    </div>
  );
}
