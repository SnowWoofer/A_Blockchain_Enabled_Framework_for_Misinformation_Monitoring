import { useState } from 'react';
import { api } from '../../api/client';
import type { Role, UserView } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { ConsoleNav } from '../../components/Layout';
import { CopyText, ErrorBox, Loading, Modal, Notice } from '../../components/ui';
import { ROLE_LABEL, formatDate, orgName } from '../../lib/format';
import { useResource } from '../../lib/useResource';

const ASSIGNABLE: Role[] = ['fact_checker', 'official', 'observer'];

export default function Users() {
  const { me } = useAuth();
  const users = useResource(() => api<UserView[]>('/api/users'), []);
  const [form, setForm] = useState({ username: '', display_name: '', role: 'fact_checker' as Role });
  const [secret, setSecret] = useState<{ username: string; password: string; reason: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  const call = <T,>(p: Promise<T>, after: (v: T) => void) => {
    setBusy(true);
    setError(undefined);
    setNotice(undefined);
    p.then((v) => { after(v); users.reload(); })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="page">
      <h1>Users of {orgName(me?.mspid)}</h1>
      <ConsoleNav />
      {notice && <Notice tone="success">{notice}</Notice>}
      {error && <ErrorBox error={error} />}

      <section className="panel">
        <h2 className="panel-title">Add a user</h2>
        <form className="inline-form" onSubmit={(e) => {
          e.preventDefault();
          call(api<{ user: UserView; temporary_password: string }>('/api/users', { method: 'POST', body: form }), (r) => {
            setSecret({ username: r.user.username, password: r.temporary_password, reason: 'created' });
            setForm({ username: '', display_name: '', role: 'fact_checker' });
          });
        }}>
          <label>Username
            <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })}
              required pattern="[a-z0-9][a-z0-9._\-]{2,63}" placeholder="e.g. thandi.m" autoCapitalize="none" spellCheck={false} />
          </label>
          <label>Display name
            <input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} maxLength={100} />
          </label>
          <label>Role
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
              {ASSIGNABLE.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
            </select>
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>Create</button>
        </form>
        <p className="muted small">The new user receives a one-time password and must choose their own at first sign-in.</p>
      </section>

      {users.loading && <Loading />}
      {users.error && <ErrorBox error={users.error} onRetry={users.reload} />}
      {users.data && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>User</th><th>Role</th><th>Status</th><th>2FA</th><th>Last sign-in</th><th /></tr></thead>
            <tbody>
              {users.data.map((u) => {
                const self = u.username === me?.subject;
                return (
                  <tr key={u.username} className={u.active ? '' : 'row-muted'}>
                    <td><strong>{u.username}</strong>{u.display_name && <div className="muted small">{u.display_name}</div>}</td>
                    <td>
                      <select value={u.role} disabled={self || busy} aria-label={`Role for ${u.username}`}
                        onChange={(e) => call(api(`/api/users/${encodeURIComponent(u.username)}`, { method: 'PATCH', body: { role: e.target.value } }),
                          () => setNotice(`${u.username} is now ${ROLE_LABEL[e.target.value as Role]}. Their sessions were signed out.`))}>
                        {ASSIGNABLE.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                      </select>
                    </td>
                    <td>
                      {!u.active ? 'Disabled' : u.locked ? 'Locked' : u.must_change_password ? 'Awaiting first sign-in' : 'Active'}
                    </td>
                    <td>{u.mfa_enabled ? 'On' : 'Off'}</td>
                    <td className="small">{formatDate(u.last_login_at)}</td>
                    <td className="row-actions">
                      {!self && (
                        <>
                          <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                            onClick={() => call(api(`/api/users/${encodeURIComponent(u.username)}`, { method: 'PATCH', body: { active: !u.active } }),
                              () => setNotice(`${u.username} ${u.active ? 'disabled and signed out' : 're-enabled'}.`))}>
                            {u.active ? 'Disable' : 'Enable'}
                          </button>
                          <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                            onClick={() => call(api<{ temporary_password: string }>(`/api/users/${encodeURIComponent(u.username)}/reset`, { method: 'POST' }),
                              (r) => setSecret({ username: u.username, password: r.temporary_password, reason: 'reset' }))}>
                            Reset access
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        open={!!secret}
        title={secret?.reason === 'reset' ? 'Access reset' : 'User created'}
        onClose={() => setSecret(undefined)}
        actions={<button type="button" className="btn btn-primary" onClick={() => setSecret(undefined)}>I have shared it securely</button>}
      >
        {secret && (
          <>
            <p>One-time password for <strong>{secret.username}</strong>:</p>
            <p className="reference"><CopyText value={secret.password} /></p>
            <p className="muted small">
              It is shown only once. Share it over a separate secure channel; the user must choose their own password
              when they first sign in.{secret.reason === 'reset' && ' Their two-factor setup was cleared and all sessions signed out.'}
            </p>
          </>
        )}
      </Modal>
    </div>
  );
}
