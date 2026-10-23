import type { Label, ReportStatus, Role, Verdict } from '../api/types';

const dateFmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const dayFmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });
const rel = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDate(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? dateFmt.format(d) : '—';
}

export function formatDay(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? dayFmt.format(d) : '—';
}

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  const d = parse(iso);
  if (!d) return '—';
  const diff = (d.getTime() - now) / 1000;
  const abs = Math.abs(diff);
  if (abs < 60) return rel.format(Math.round(diff), 'second');
  if (abs < 3600) return rel.format(Math.round(diff / 60), 'minute');
  if (abs < 86400) return rel.format(Math.round(diff / 3600), 'hour');
  return rel.format(Math.round(diff / 86400), 'day');
}

export function isPast(iso: string | null | undefined, now = Date.now()): boolean {
  const d = parse(iso);
  return !!d && d.getTime() < now;
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  misinformation: 'Misinformation',
  not_misinformation: 'Not misinformation',
  not_confirmed: 'Not confirmed',
  expired: 'Expired without verdict',
  under_review: 'Under review',
};

export const VERDICT_EXPLAINER: Record<Verdict, string> = {
  misinformation: 'At least two-thirds of the consortium independently found this claim to be misinformation.',
  not_misinformation: 'After the review window closed, most fact-checking organisations found this claim was not misinformation.',
  not_confirmed: 'The consortium could not reach the two-thirds majority needed to confirm this claim as misinformation.',
  expired: 'The review window closed before fact-checkers reached a decision.',
  under_review: 'Fact-checkers are still reviewing this claim.',
};

export const STATUS_LABEL: Record<ReportStatus, string> = {
  PENDING: 'Awaiting review',
  UNDER_REVIEW: 'Under review',
  FINAL: 'Final',
  REJECTED: 'Not confirmed',
  EXPIRED: 'Expired',
};

export const OUTCOME_LABEL: Record<Label, string> = {
  '1': 'Misinformation',
  '0': 'Not misinformation',
};

export const ROLE_LABEL: Record<Role, string> = {
  official: 'Official',
  fact_checker: 'Fact-checker',
  observer: 'Observer',
  ai_submitter: 'AI pipeline',
};

export function verdictOf(status: ReportStatus, finalLabel?: Label | null): Verdict {
  if (status === 'FINAL') return finalLabel === '1' ? 'misinformation' : 'not_misinformation';
  if (status === 'REJECTED') return 'not_confirmed';
  if (status === 'EXPIRED') return 'expired';
  return 'under_review';
}

export function shortId(id: string, keep = 10): string {
  return id.length <= keep + 4 ? id : `${id.slice(0, keep)}…${id.slice(-4)}`;
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`;
}

export function orgName(mspid: string | null | undefined): string {
  if (!mspid) return '—';
  const m = /^Org(\d+)MSP$/.exec(mspid);
  return m ? `Organisation ${m[1]}` : mspid;
}

export function platformLabel(platform: string | null | undefined): string {
  if (!platform) return 'Unknown source';
  const known: Record<string, string> = {
    public_tip: 'Public tip', ai_scout: 'AI scout', loadtest: 'Load test', test: 'Test feed',
  };
  return known[platform] ?? platform;
}

export function isSafeHttpUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return (u.protocol === 'https:' || u.protocol === 'http:') && !!u.hostname && !u.username;
  } catch {
    return false;
  }
}
