import type { Session } from './types';

/**
 * Same-origin API client.
 *
 * The access token lives only in this module's memory (never localStorage),
 * so injected script cannot lift it from storage and it is gone when the
 * tab closes. The long-lived refresh token is an HttpOnly, SameSite=Strict
 * cookie scoped to /api/auth that page script can never read; the
 * X-Requested-With header on every call is the gateway's second CSRF check.
 */

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type SessionListener = (session: Session | null) => void;

let accessToken: string | null = null;
let refreshTimer: number | undefined;
let refreshInFlight: Promise<Session | null> | null = null;
const listeners = new Set<SessionListener>();

export function onSessionChange(fn: SessionListener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setSession(session: Session | null) {
  accessToken = session?.access_token ?? null;
  window.clearTimeout(refreshTimer);
  if (session) {
    // Renew a minute before expiry so an active user never sees a 401.
    const ms = Math.max(30, session.expires_in - 60) * 1000;
    refreshTimer = window.setTimeout(() => void refreshSession(), ms);
  }
  listeners.forEach((fn) => fn(session));
}

function messageFrom(detail: unknown, fallback: string): { message: string; code?: string } {
  if (typeof detail === 'string') return { message: detail };
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg}, ...]
    const first = detail[0] as { msg?: string; loc?: unknown[] } | undefined;
    if (first?.msg) {
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : undefined;
      return { message: field && typeof field === 'string' ? `${field}: ${first.msg}` : first.msg };
    }
  }
  if (detail && typeof detail === 'object') {
    const d = detail as { message?: string; code?: string };
    if (d.message) return { message: d.message, code: d.code };
  }
  return { message: fallback };
}

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set('X-Requested-With', 'XMLHttpRequest');
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (accessToken && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${accessToken}`);
  return fetch(path, { ...init, headers, credentials: 'same-origin', cache: 'no-store' });
}

async function parse<T>(resp: Response): Promise<T> {
  const text = await resp.text();
  let data: { detail?: unknown } | null = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // e.g. an HTML error page from the edge proxy
    if (resp.ok) throw new ApiError(resp.status, 'Unexpected response from server');
  }
  if (!resp.ok) {
    const fallback = resp.status === 429
      ? 'Too many requests. Please wait a moment and try again.'
      : resp.status >= 502 ? 'The service is temporarily unavailable.' : `Request failed (${resp.status})`;
    const { message, code } = messageFrom(data?.detail, fallback);
    throw new ApiError(resp.status, message, code);
  }
  return data as T;
}

export function refreshSession(): Promise<Session | null> {
  if (!refreshInFlight) {
    refreshInFlight = rawFetch('/api/auth/refresh', { method: 'POST' })
      .then(async (resp) => {
        if (!resp.ok) {
          setSession(null);
          return null;
        }
        const session = (await resp.json()) as Session;
        setSession(session);
        return session;
      })
      .catch(() => {
        setSession(null);
        return null;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

interface Options {
  method?: 'GET' | 'POST' | 'PATCH';
  body?: unknown;
  query?: Record<string, string | number | undefined | null>;
}

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const qs = new URLSearchParams();
  Object.entries(opts.query ?? {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  });
  const url = qs.toString() ? `${path}?${qs}` : path;
  const init: RequestInit = {
    method: opts.method ?? 'GET',
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  };
  let resp = await rawFetch(url, init);
  if (resp.status === 401 && accessToken) {
    // Access token expired mid-session: renew once and replay.
    const renewed = await refreshSession();
    if (renewed) resp = await rawFetch(url, init);
  }
  return parse<T>(resp);
}

export async function login(username: string, password: string, otp?: string): Promise<Session> {
  const resp = await rawFetch('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password, otp: otp || undefined }),
  });
  const session = await parse<Session>(resp);
  setSession(session);
  return session;
}

export async function logout(): Promise<void> {
  try {
    await rawFetch('/api/auth/logout', { method: 'POST' });
  } finally {
    setSession(null);
  }
}

/** Public tip line: forwarded by the edge proxy to the claim-ingest worker. */
export async function submitTip(claim: string): Promise<{ reference: string; status: string }> {
  const resp = await fetch('/tips', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    body: JSON.stringify({ claim }),
    credentials: 'omit',
  });
  return parse(resp);
}
