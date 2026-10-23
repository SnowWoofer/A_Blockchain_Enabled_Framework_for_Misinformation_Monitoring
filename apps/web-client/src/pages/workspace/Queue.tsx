import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api/client';
import type { Claim, Page } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { WorkspaceNav } from '../../components/Layout';
import { ConfidenceBar, Empty, ErrorBox, Loading, OutcomeBadge, Pagination, StatusBadge } from '../../components/ui';
import { isPast, orgName, platformLabel, relativeTime } from '../../lib/format';
import { useResource } from '../../lib/useResource';

const PAGE_SIZE = 15;

export function ClaimRow({ claim }: { claim: Claim }) {
  const overdue = isPast(claim.fact_check_deadline);
  return (
    <Link to={`/workspace/claims/${encodeURIComponent(claim.id)}`} className="card claim-row">
      <div className="card-top">
        <StatusBadge status={claim.status} />
        {claim.my_org_vote && <span className="small">Your org: <OutcomeBadge outcome={claim.my_org_vote} /></span>}
        <span className={`small ${overdue ? 'text-warn' : 'muted'}`}>
          {claim.status === 'PENDING' || claim.status === 'UNDER_REVIEW'
            ? (overdue ? 'Window closed ' : 'Closes ') + relativeTime(claim.fact_check_deadline)
            : 'Closed'}
        </span>
      </div>
      <p className="claim-excerpt">{claim.content}</p>
      <ConfidenceBar label={claim.inference_label} confidence={claim.confidence} />
      <div className="card-meta muted small">
        <span>{claim.consensus.votes_cast} of {claim.consensus.eligible_voters} organisations voted</span>
        <span>·</span>
        <span>Raised by {orgName(claim.submitted_by)}</span>
        <span>·</span>
        <span>{platformLabel(claim.source_platform)}</span>
      </div>
    </Link>
  );
}

export default function Queue() {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const sort = params.get('sort') ?? 'deadline';
  const aiLabel = params.get('ai') ?? '';
  const page = Math.max(1, Number(params.get('page') ?? '1') || 1);
  const { data, error, loading, reload } = useResource(
    () => api<Page<Claim>>('/fc/claims/pending', {
      query: { sort, ai_label: aiLabel, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE },
    }),
    [sort, aiLabel, page],
  );
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    if (k !== 'page') next.delete('page');
    setParams(next);
  };

  return (
    <div className="page">
      <div className="section-head">
        <div>
          <h1>Review queue</h1>
          <p className="muted">Claims awaiting {orgName(me?.mspid)}'s vote, excluding those your organisation raised.</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={reload}>Refresh</button>
      </div>
      <WorkspaceNav />
      <div className="toolbar">
        <label>Sort
          <select value={sort} onChange={(e) => set('sort', e.target.value)}>
            <option value="deadline">Closing soonest</option>
            <option value="confidence">AI confidence</option>
            <option value="newest">Newest</option>
          </select>
        </label>
        <label>AI screening
          <select value={aiLabel} onChange={(e) => set('ai', e.target.value)}>
            <option value="">Any</option>
            <option value="1">Flagged as misinformation</option>
            <option value="0">Not flagged</option>
          </select>
        </label>
        {data && <span className="muted small">{data.total} waiting</span>}
      </div>
      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && !loading && (data.items.length === 0 ? (
        <Empty title="Nothing waiting for your organisation">
          <p>New claims appear here as the AI screening flags them.</p>
        </Empty>
      ) : (
        <div className="card-list">{data.items.map((c) => <ClaimRow key={c.id} claim={c} />)}</div>
      ))}
      {data && <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={(p) => set('page', String(p))} />}
    </div>
  );
}
