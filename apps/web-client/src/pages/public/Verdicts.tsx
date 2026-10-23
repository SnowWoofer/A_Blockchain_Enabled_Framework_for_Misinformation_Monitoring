import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api/client';
import type { Page, PublicSummary, Verdict } from '../../api/types';
import { Empty, ErrorBox, Loading, Pagination, VerdictBadge } from '../../components/ui';
import { VERDICT_LABEL, formatDay, platformLabel } from '../../lib/format';
import { useResource } from '../../lib/useResource';

const FILTERS: (Verdict | '')[] = ['', 'misinformation', 'not_misinformation', 'not_confirmed', 'expired'];
const PAGE_SIZE = 10;

export function VerdictCard({ item }: { item: PublicSummary }) {
  return (
    <Link to={`/verdicts/${encodeURIComponent(item.id)}`} className="card verdict-card">
      <div className="card-top">
        <VerdictBadge verdict={item.verdict} />
        <span className="muted small">{formatDay(item.closed_at || item.submitted_at)}</span>
      </div>
      <p className="claim-excerpt">{item.claim || 'Claim text unavailable'}</p>
      <div className="card-meta muted small">
        <span>{item.votes.yes} of {item.votes.required_yes} confirmations needed</span>
        <span>·</span>
        <span>{item.votes.cast} organisations voted</span>
        <span>·</span>
        <span>{platformLabel(item.platform)}</span>
      </div>
    </Link>
  );
}

export default function Verdicts() {
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const verdict = (params.get('verdict') ?? '') as Verdict | '';
  const page = Math.max(1, Number(params.get('page') ?? '1') || 1);
  const [draft, setDraft] = useState(q);
  useEffect(() => setDraft(q), [q]);

  const { data, error, loading, reload } = useResource(
    () => api<Page<PublicSummary>>('/api/public/reports', { query: { q, verdict, page, page_size: PAGE_SIZE } }),
    [q, verdict, page],
  );

  const update = (next: Record<string, string>) => {
    const merged = new URLSearchParams(params);
    Object.entries(next).forEach(([k, v]) => (v ? merged.set(k, v) : merged.delete(k)));
    setParams(merged);
  };

  return (
    <div className="page">
      <h1>Published verdicts</h1>
      <p className="lead">Only claims the consortium has finished reviewing appear here. Claims still under review are never published.</p>
      <form className="search" role="search" onSubmit={(e) => { e.preventDefault(); update({ q: draft.trim(), page: '' }); }}>
        <label htmlFor="verdict-q" className="sr-only">Search claims</label>
        <input id="verdict-q" type="search" value={draft} onChange={(e) => setDraft(e.target.value)}
          placeholder="Search claim text" maxLength={200} />
        <button type="submit" className="btn btn-primary">Search</button>
      </form>
      <div className="chips" role="group" aria-label="Filter by verdict">
        {FILTERS.map((f) => (
          <button key={f || 'all'} type="button" className={`chip${verdict === f ? ' chip-on' : ''}`}
            aria-pressed={verdict === f} onClick={() => update({ verdict: f, page: '' })}>
            {f ? VERDICT_LABEL[f] : 'All'}
          </button>
        ))}
      </div>

      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && !loading && (
        <>
          <p className="muted small">{data.total} {data.total === 1 ? 'verdict' : 'verdicts'}{q && <> matching “{q}”</>}</p>
          {data.items.length === 0 ? (
            <Empty title={q ? 'No published verdict matches that search' : 'Nothing here yet'}>
              {q && <p>The claim may not have been reviewed yet. <Link to="/submit">Report it</Link> so fact-checkers can take a look.</p>}
            </Empty>
          ) : (
            <div className="card-list">
              {data.items.map((item) => <VerdictCard key={item.id} item={item} />)}
            </div>
          )}
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={(p) => update({ page: String(p) })} />
        </>
      )}
    </div>
  );
}
