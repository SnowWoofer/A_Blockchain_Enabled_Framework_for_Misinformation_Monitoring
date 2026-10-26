import { Link, useParams } from 'react-router-dom';
import { ApiError, api } from '../../api/client';
import type { PublicDetail } from '../../api/types';
import { HistoryTimeline, IntegrityResult } from '../../components/LedgerPanels';
import {
  ConfidenceBar, ConsensusMeter, CopyText, Empty, ErrorBox, EvidenceLinks, Loading, OutcomeBadge, VerdictBadge,
} from '../../components/ui';
import { VERDICT_EXPLAINER, formatDate, orgName, platformLabel, shortId } from '../../lib/format';
import { useResource } from '../../lib/useResource';

export default function VerdictDetail() {
  const { id = '' } = useParams();
  const { data, error, loading, reload } = useResource(
    () => api<PublicDetail>(`/api/public/reports/${encodeURIComponent(id)}`), [id]);

  if (loading) return <div className="page"><Loading /></div>;
  if (error instanceof ApiError && error.status === 404) {
    return (
      <div className="page narrow">
        <Empty title="No published verdict">
          <p>This claim either does not exist or is still under review. Verdicts are published only once review is complete.</p>
          <Link to="/verdicts">Browse published verdicts</Link>
        </Empty>
      </div>
    );
  }
  if (error || !data) return <div className="page"><ErrorBox error={error ?? 'Not found'} onRetry={reload} /></div>;

  return (
    <div className="page">
      <Link to="/verdicts" className="back">← All verdicts</Link>
      <section className={`verdict-hero verdict-hero-${data.verdict}`}>
        <VerdictBadge verdict={data.verdict} large />
        <p>{VERDICT_EXPLAINER[data.verdict]}</p>
        <p className="muted small">Closed {formatDate(data.closed_at || data.deadline)}</p>
      </section>

      <div className="two-col">
        <div>
          <section className="panel">
            <h2 className="panel-title">The claim</h2>
            <blockquote className="claim">{data.claim}</blockquote>
            <p className="muted small">Source: {platformLabel(data.platform)}{data.published_at && <> · first seen {formatDate(data.published_at)}</>}</p>
          </section>

          <section className="panel">
            <h2 className="panel-title">How the consortium voted</h2>
            <ConsensusMeter yes={data.votes.yes} no={data.votes.no} cast={data.votes.cast}
              eligible={data.votes.registered_orgs} required={data.votes.required_yes} />
            <ul className="factchecks">
              {data.fact_checks.map((fc) => (
                <li key={fc.tx_id} className="factcheck">
                  <div className="factcheck-head">
                    <strong>{orgName(fc.org)}</strong>
                    <OutcomeBadge outcome={fc.outcome} />
                  </div>
                  {fc.reasoning ? <p className="reasoning">{fc.reasoning}</p> : <p className="muted small">No written reasoning was published.</p>}
                  <EvidenceLinks links={fc.evidence} />
                  <p className="muted small">{fc.timestamp && formatDate(fc.timestamp)} · tx {shortId(fc.tx_id, 10)}</p>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <aside>
          <section className="panel">
            <h2 className="panel-title">Initial AI screening</h2>
            <ConfidenceBar label={data.ai_label} confidence={data.ai_confidence} />
            <p className="muted small">
              The AI only decides which claims fact-checkers look at. The verdict above comes from the
              organisations' votes. Model: {data.model_version}.
            </p>
          </section>
          {data.integrity && (
            <section className="panel">
              <h2 className="panel-title">Has this record been altered?</h2>
              <IntegrityResult result={data.integrity} />
            </section>
          )}
          <section className="panel">
            <h2 className="panel-title">Blockchain record</h2>
            <dl className="kv">
              <dt>Report ID</dt><dd><CopyText value={data.id} display={shortId(data.id, 14)} /></dd>
            </dl>
            <HistoryTimeline items={data.history} />
          </section>
        </aside>
      </div>
    </div>
  );
}
