import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { Label, ReportStatus, Verdict } from '../api/types';
import { OUTCOME_LABEL, STATUS_LABEL, VERDICT_LABEL, isSafeHttpUrl, percent } from '../lib/format';

export function VerdictBadge({ verdict, large = false }: { verdict: Verdict; large?: boolean }) {
  return <span className={`badge verdict-${verdict}${large ? ' badge-lg' : ''}`}>{VERDICT_LABEL[verdict]}</span>;
}

export function StatusBadge({ status }: { status: ReportStatus }) {
  return <span className={`badge status-${status.toLowerCase()}`}>{STATUS_LABEL[status]}</span>;
}

export function OutcomeBadge({ outcome }: { outcome: Label }) {
  return (
    <span className={`badge ${outcome === '1' ? 'verdict-misinformation' : 'verdict-not_misinformation'}`}>
      {OUTCOME_LABEL[outcome]}
    </span>
  );
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return <div className="loading" role="status"><span className="spinner" aria-hidden="true" />{label}</div>;
}

export function ErrorBox({ error, onRetry }: { error: Error | string; onRetry?: () => void }) {
  return (
    <div className="notice notice-error" role="alert">
      <span>{typeof error === 'string' ? error : error.message}</span>
      {onRetry && <button type="button" className="btn btn-ghost btn-sm" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Notice({ tone = 'info', children }: { tone?: 'info' | 'success' | 'warning' | 'error'; children: ReactNode }) {
  return <div className={`notice notice-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>{children}</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body">{children}</div>}
    </div>
  );
}

/** The AI's screening score, labelled so it is never mistaken for a verdict. */
export function ConfidenceBar({ label, confidence }: { label: Label; confidence: number }) {
  return (
    <div className="confidence">
      <div className="confidence-head">
        <span>AI screening: <strong>{OUTCOME_LABEL[label]}</strong></span>
        <span className="muted">{percent(confidence)} confidence</span>
      </div>
      <div className="meter" aria-hidden="true">
        <div className={`meter-fill ${label === '1' ? 'fill-warn' : 'fill-ok'}`} style={{ width: `${Math.round(confidence * 100)}%` }} />
      </div>
    </div>
  );
}

interface MeterProps {
  yes?: number;
  no?: number;
  cast: number;
  eligible: number;
  required: number;
  blind?: boolean;
}

/** Progress towards the chaincode's 2/3 quorum. */
export function ConsensusMeter({ yes, no, cast, eligible, required, blind }: MeterProps) {
  const total = Math.max(eligible, 1);
  const thresholdPct = Math.min(100, (required / total) * 100);
  return (
    <div className="consensus">
      <div className="consensus-bar" role="img"
        aria-label={blind ? `${cast} of ${eligible} organisations have voted` : `${yes ?? 0} misinformation and ${no ?? 0} not-misinformation votes of ${eligible} eligible; ${required} needed to confirm`}>
        {blind ? (
          <div className="seg seg-hidden" style={{ width: `${(cast / total) * 100}%` }} />
        ) : (
          <>
            <div className="seg seg-yes" style={{ width: `${((yes ?? 0) / total) * 100}%` }} />
            <div className="seg seg-no" style={{ width: `${((no ?? 0) / total) * 100}%` }} />
          </>
        )}
        <div className="threshold" style={{ left: `${thresholdPct}%` }} title={`${required} votes confirm misinformation`} />
      </div>
      <div className="consensus-legend">
        {blind ? (
          <span>{cast} of {eligible} eligible organisations have voted</span>
        ) : (
          <>
            <span><i className="dot dot-yes" />{yes ?? 0} misinformation</span>
            <span><i className="dot dot-no" />{no ?? 0} not misinformation</span>
            <span className="muted">{eligible - cast} yet to vote</span>
          </>
        )}
        <span className="muted">{required} needed to confirm</span>
      </div>
    </div>
  );
}

export function EvidenceLinks({ links }: { links: string[] }) {
  const safe = links.filter(isSafeHttpUrl);
  if (!safe.length) return null;
  return (
    <ul className="evidence">
      {safe.map((url) => (
        <li key={url}>
          {/* noreferrer: evidence sites learn nothing about which claim sent the reader */}
          <a href={url} target="_blank" rel="noopener noreferrer nofollow">{new URL(url).hostname}</a>
          <span className="muted evidence-path">{new URL(url).pathname !== '/' ? new URL(url).pathname : ''}</span>
        </li>
      ))}
    </ul>
  );
}

export function CopyText({ value, display }: { value: string; display?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="copy">
      <code title={value}>{display ?? value}</code>
      <button
        type="button"
        className="btn btn-ghost btn-xs"
        onClick={() => {
          void navigator.clipboard?.writeText(value).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </span>
  );
}

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
}

/** Native <dialog>: focus trapping and Esc-to-close come for free. */
export function Modal({ open, title, onClose, children, actions }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="modal" onClose={onClose} aria-labelledby="modal-title">
      <h2 id="modal-title" className="modal-title">{title}</h2>
      <div className="modal-body">{children}</div>
      {actions && <div className="modal-actions">{actions}</div>}
    </dialog>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <nav className="pagination" aria-label="Pagination">
      <button type="button" className="btn btn-ghost btn-sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span className="muted">Page {page} of {pages}</span>
      <button type="button" className="btn btn-ghost btn-sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </nav>
  );
}

export function Stat({ value, label, tone }: { value: number | string; label: string; tone?: string }) {
  return (
    <div className={`stat${tone ? ` stat-${tone}` : ''}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
