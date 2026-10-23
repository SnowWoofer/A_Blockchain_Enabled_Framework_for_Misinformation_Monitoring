import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { Page, PublicInfo, PublicStats, PublicSummary } from '../../api/types';
import { Stat } from '../../components/ui';
import { useResource } from '../../lib/useResource';
import { VerdictCard } from './Verdicts';

export default function Home() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const stats = useResource(() => api<PublicStats>('/api/public/stats'), []);
  const info = useResource(() => api<PublicInfo>('/api/public/info'), []);
  const recent = useResource(() => api<Page<PublicSummary>>('/api/public/reports', { query: { page_size: 5 } }), []);
  const by = stats.data?.by_verdict ?? {};

  return (
    <div className="page">
      <section className="hero">
        <h1>Has this claim been fact-checked?</h1>
        <p className="lead">
          Search claims reviewed by an independent consortium of fact-checking organisations. Every vote is
          recorded on a blockchain, so verdicts cannot be quietly changed after the fact.
        </p>
        <form
          className="search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            navigate(`/verdicts${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''}`);
          }}
        >
          <label htmlFor="home-q" className="sr-only">Search claims</label>
          <input id="home-q" type="search" value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Paste or type words from the claim" maxLength={200} />
          <button type="submit" className="btn btn-primary">Search</button>
        </form>
      </section>

      <section className="stats" aria-label="Consortium at a glance">
        <Stat value={by.misinformation ?? 0} label="Confirmed misinformation" tone="misinformation" />
        <Stat value={by.not_confirmed ?? 0} label="Not confirmed" tone="not_confirmed" />
        <Stat value={by.under_review ?? 0} label="Under review" />
        <Stat value={stats.data?.registered_orgs ?? '—'} label="Member organisations" />
      </section>

      <div className="two-col">
        <section>
          <div className="section-head">
            <h2>Recent verdicts</h2>
            <Link to="/verdicts">All verdicts</Link>
          </div>
          {recent.data?.items.length === 0 && <p className="muted">No verdicts have been published yet.</p>}
          <div className="card-list">
            {recent.data?.items.map((item) => <VerdictCard key={item.id} item={item} />)}
          </div>
        </section>

        <aside className="panel how">
          <h2>How a verdict is reached</h2>
          <ol className="steps">
            <li><strong>Screening.</strong> An AI model (AfroXLM-R, fine-tuned on claims in South African languages) flags potentially misleading claims, including tips from the public.</li>
            <li><strong>Independent review.</strong> Each member organisation investigates and casts one vote, without seeing the others' verdicts first. The organisation that raised a claim cannot vote on it.</li>
            <li><strong>Two-thirds rule.</strong> A claim is confirmed as misinformation only when {info.data ? `${info.data.required_yes} of ${info.data.registered_orgs}` : 'two-thirds of'} member organisations agree.</li>
            <li><strong>Tamper-evident record.</strong> The claim's fingerprint and every vote are written to a Hyperledger Fabric blockchain, so anyone can check that a published claim was not altered.</li>
          </ol>
          <Link to="/submit" className="btn btn-secondary">Report a suspicious claim</Link>
        </aside>
      </div>
    </div>
  );
}
