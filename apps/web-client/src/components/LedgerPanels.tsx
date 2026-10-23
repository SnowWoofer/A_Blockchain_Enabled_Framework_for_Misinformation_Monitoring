import { useState } from 'react';
import { api } from '../api/client';
import type { HistoryEntry, ReportStatus, Verification } from '../api/types';
import { formatDate, orgName, shortId } from '../lib/format';
import { useResource } from '../lib/useResource';
import { CopyText, ErrorBox, Loading, StatusBadge } from './ui';

/** Re-hashes the off-chain document and compares it with the ledger anchor. */
export function IntegrityResult({ result }: { result: Verification }) {
  return (
    <div className={`integrity ${result.verified ? 'integrity-ok' : 'integrity-bad'}`}>
      <p className="integrity-head">
        <span aria-hidden="true">{result.verified ? '✓' : '✕'}</span>
        {result.verified ? 'Tamper check passed' : 'Tamper check failed'}
      </p>
      <ul className="checklist">
        <li className={result.off_chain_intact ? 'ok' : 'bad'}>
          Stored claim document is unmodified (its contents hash to its recorded fingerprint)
        </li>
        <li className={result.matches_on_chain ? 'ok' : 'bad'}>
          That fingerprint matches the one anchored on the blockchain at submission
        </li>
      </ul>
      <dl className="kv">
        <dt>Document fingerprint</dt><dd><CopyText value={result.content_hash ?? ''} display={shortId(result.content_hash ?? '', 16)} /></dd>
        <dt>On-chain fingerprint</dt><dd><CopyText value={result.on_chain_hash ?? ''} display={shortId(result.on_chain_hash ?? '', 16)} /></dd>
      </dl>
      <p className="muted small">{result.explanation}</p>
    </div>
  );
}

export function IntegrityCheck({ reportId }: { reportId: string }) {
  const [state, setState] = useState<{ loading?: boolean; result?: Verification; error?: Error }>({});
  return (
    <section className="panel">
      <h3 className="panel-title">Integrity</h3>
      {!state.result && (
        <p className="muted">Confirm that the claim text you are reviewing is exactly what was anchored on the ledger.</p>
      )}
      {state.result && <IntegrityResult result={state.result} />}
      {state.error && <ErrorBox error={state.error} />}
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        disabled={state.loading}
        onClick={() => {
          setState({ loading: true });
          api<Verification>(`/api/reports/${encodeURIComponent(reportId)}/verify`)
            .then((result) => setState({ result }))
            .catch((error: Error) => setState({ error }));
        }}
      >
        {state.loading ? 'Checking…' : state.result ? 'Check again' : 'Run tamper check'}
      </button>
    </section>
  );
}

export function HistoryTimeline({ items }: { items: { tx_id: string; timestamp: string; status?: ReportStatus; note?: string }[] }) {
  return (
    <ol className="timeline">
      {items.map((h) => (
        <li key={h.tx_id}>
          <div className="timeline-head">
            {h.status && <StatusBadge status={h.status} />}
            <time className="muted small" dateTime={h.timestamp}>{formatDate(h.timestamp)}</time>
          </div>
          {h.note && <div className="small">{h.note}</div>}
          <div className="small muted">Transaction <CopyText value={h.tx_id} display={shortId(h.tx_id, 12)} /></div>
        </li>
      ))}
    </ol>
  );
}

function describe(entry: HistoryEntry, prev?: HistoryEntry): string {
  const rec = entry.record;
  if (!rec) return entry.is_delete ? 'Record deleted' : '';
  if (!prev) return `Submitted by ${orgName(rec.submitted_by)}`;
  const before = prev.record?.fact_checks.length ?? 0;
  if (rec.fact_checks.length > before) {
    const latest = rec.fact_checks[rec.fact_checks.length - 1];
    return `${orgName(latest.checker_msp)} cast its vote`;
  }
  if (rec.finalized_by) return `Closed by ${orgName(rec.finalized_by)}`;
  return 'Record updated';
}

export function LedgerHistory({ reportId }: { reportId: string }) {
  const { data, error, loading, reload } = useResource(
    () => api<HistoryEntry[]>(`/api/reports/${encodeURIComponent(reportId)}/history`), [reportId]);
  return (
    <section className="panel">
      <h3 className="panel-title">Ledger history</h3>
      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && (
        <HistoryTimeline items={data.map((h, i) => ({
          tx_id: h.tx_id, timestamp: h.timestamp, status: h.record?.status, note: describe(h, data[i - 1]),
        }))} />
      )}
    </section>
  );
}
