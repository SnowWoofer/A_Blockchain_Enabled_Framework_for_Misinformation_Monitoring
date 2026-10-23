import qrcode from 'qrcode-generator';
import { useMemo, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { CopyText, ErrorBox, Loading, Notice } from '../components/ui';
import { ROLE_LABEL, formatDate, orgName } from '../lib/format';

function PasswordForm({ forced, onDone }: { forced: boolean; onDone: () => void }) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [done, setDone] = useState(false);
  const mismatch = confirm.length > 0 && next !== confirm;
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault();
        if (mismatch) return;
        setBusy(true);
        setError(undefined);
        api('/api/auth/password', { method: 'POST', body: { current_password: current, new_password: next } })
          .then(() => { setDone(true); setCurrent(''); setNext(''); setConfirm(''); onDone(); })
          .catch((err: Error) => setError(err.message))
          .finally(() => setBusy(false));
      }}
    >
      {done && <Notice tone="success">Password changed. Your other sessions have been signed out.</Notice>}
      <label htmlFor="cur">{forced ? 'One-time password' : 'Current password'}</label>
      <input id="cur" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
      <label htmlFor="new">New password</label>
      <input id="new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)}
        required minLength={12} maxLength={72} aria-describedby="pw-help" />
      <p id="pw-help" className="muted small">At least 12 characters. A short phrase of unrelated words is easy to remember and hard to guess.</p>
      <label htmlFor="confirm">Repeat new password</label>
      <input id="confirm" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required
        aria-invalid={mismatch} />
      {mismatch && <p className="small text-warn">The passwords do not match.</p>}
      {error && <ErrorBox error={error} />}
      <button type="submit" className="btn btn-primary" disabled={busy || mismatch}>{busy ? 'Saving…' : 'Change password'}</button>
    </form>
  );
}

function QrCode({ uri }: { uri: string }) {
  const src = useMemo(() => {
    const qr = qrcode(0, 'M');
    qr.addData(uri);
    qr.make();
    return qr.createDataURL(5, 8);
  }, [uri]);
  return <img className="qr" src={src} alt="QR code for your authenticator app" width={220} height={220} />;
}

function TwoFactor({ enabled, onChange }: { enabled: boolean; onChange: () => void }) {
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string }>();
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const run = (p: Promise<unknown>, after: () => void) => {
    setBusy(true);
    setError(undefined);
    p.then(after).catch((err: Error) => setError(err.message)).finally(() => setBusy(false));
  };

  if (enabled) {
    return (
      <form className="form" onSubmit={(e) => {
        e.preventDefault();
        run(api('/api/auth/mfa/disable', { method: 'POST', body: { password, code } }), () => { setPassword(''); setCode(''); onChange(); });
      }}>
        <Notice tone="success">Two-factor authentication is on.</Notice>
        <p className="muted small">To turn it off, confirm with your password and a current code.</p>
        <label htmlFor="mfa-pw">Password</label>
        <input id="mfa-pw" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        <label htmlFor="mfa-off">Authentication code</label>
        <input id="mfa-off" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required />
        {error && <ErrorBox error={error} />}
        <button type="submit" className="btn btn-danger" disabled={busy}>Turn off two-factor</button>
      </form>
    );
  }
  if (!setup) {
    return (
      <div>
        <p>Protect your account with a code from an authenticator app (for example Google Authenticator, Microsoft Authenticator or Aegis). Strongly recommended for officials.</p>
        {error && <ErrorBox error={error} />}
        <button type="button" className="btn btn-secondary" disabled={busy}
          onClick={() => run(api<{ secret: string; otpauth_uri: string }>('/api/auth/mfa/setup', { method: 'POST' }).then(setSetup), () => undefined)}>
          Set up two-factor
        </button>
      </div>
    );
  }
  return (
    <form className="form" onSubmit={(e) => {
      e.preventDefault();
      run(api('/api/auth/mfa/enable', { method: 'POST', body: { code } }), () => { setSetup(undefined); setCode(''); onChange(); });
    }}>
      <ol className="steps">
        <li>Scan this code with your authenticator app.<QrCode uri={setup.otpauth_uri} /></li>
        <li>Can't scan it? Enter this key manually: <CopyText value={setup.secret} /></li>
        <li>Enter the 6-digit code the app shows to finish.</li>
      </ol>
      <label htmlFor="mfa-code">Authentication code</label>
      <input id="mfa-code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required autoFocus />
      {error && <ErrorBox error={error} />}
      <button type="submit" className="btn btn-primary" disabled={busy}>Turn on two-factor</button>
    </form>
  );
}

export default function Account() {
  const { me, status, reload, logout } = useAuth();
  const navigate = useNavigate();
  if (status === 'loading') return <div className="page"><Loading /></div>;
  if (!me) return <Navigate to="/login" replace />;
  const forced = me.must_change_password;
  const user = me.user;

  if (forced) {
    return (
      <div className="page narrow">
        <h1>Choose your password</h1>
        <Notice tone="warning">You signed in with a one-time password. Choose your own password to continue.</Notice>
        <PasswordForm forced onDone={() => { void reload().then(() => navigate('/workspace')); }} />
      </div>
    );
  }

  return (
    <div className="page narrow">
      <h1>Your account</h1>
      <section className="panel">
        <dl className="kv">
          <dt>Username</dt><dd>{me.subject}</dd>
          <dt>Organisation</dt><dd>{orgName(me.mspid)} ({me.mspid})</dd>
          <dt>Role</dt><dd>{ROLE_LABEL[me.role]}</dd>
          {user && <><dt>Last sign-in</dt><dd>{formatDate(user.last_login_at)}</dd></>}
        </dl>
      </section>
      {me.kind === 'user' && (
        <>
          <section className="panel">
            <h2 className="panel-title">Two-factor authentication</h2>
            <TwoFactor enabled={!!user?.mfa_enabled} onChange={() => void reload()} />
          </section>
          <section className="panel">
            <h2 className="panel-title">Change password</h2>
            <PasswordForm forced={false} onDone={() => undefined} />
          </section>
        </>
      )}
      <button type="button" className="btn btn-ghost" onClick={() => { void logout().then(() => navigate('/')); }}>Sign out</button>
    </div>
  );
}
