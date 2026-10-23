import { useState } from 'react';
import { api } from '../../api/client';
import type { Claim, Page } from '../../api/types';
import { WorkspaceNav } from '../../components/Layout';
import { Empty, ErrorBox, Loading, Pagination } from '../../components/ui';
import { useResource } from '../../lib/useResource';
import { ClaimRow } from './Queue';

const PAGE_SIZE = 15;

export default function Reviewed() {
  const [page, setPage] = useState(1);
  const { data, error, loading, reload } = useResource(
    () => api<Page<Claim>>('/fc/claims/reviewed', { query: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE } }),
    [page],
  );
  return (
    <div className="page">
      <h1>Reviewed by my organisation</h1>
      <WorkspaceNav />
      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && !loading && (data.items.length === 0
        ? <Empty title="Your organisation has not voted on any claims yet" />
        : <div className="card-list">{data.items.map((c) => <ClaimRow key={c.id} claim={c} />)}</div>)}
      {data && <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />}
    </div>
  );
}
