import { useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { ErrorBox } from '../components/ui';

export default function Login() {
  const { login, me } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? '/workspace';
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [otp, setOtp] = useState('');
  const [needOtp, setNeedOtp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  if (me) return <Navigate to={me.must_change_password ? '/account' : from} replace />;

  return (
    <div className="page narrow">
      <h1>Sign in</h1>
      <p className="lead">For fact-checkers and officials of member organisations. Accounts are issued by an official in your organisation.</p>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setError(undefined);
          login(username, password, needOtp ? otp : undefined)
            .then(() => navigate(from, { replace: true }))
            .catch((err: unknown) => {
              if (err instanceof ApiError && err.code === 'mfa_required') {
                setNeedOtp(true);
                setError(undefined);
              } else if (err instanceof ApiError && err.status === 423) {
                setError('This account is temporarily locked after too many failed attempts. Try again in 15 minutes or ask an official to reset it.');
              } else {
                setError(err instanceof Error ? err.message : 'Sign-in failed');
                if (err instanceof ApiError && err.code === 'mfa_invalid') setOtp('');
              }
            })
            .finally(() => setBusy(false));
        }}
      >
        <label htmlFor="username">Username</label>
        <input id="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)}
          required maxLength={64} autoCapitalize="none" spellCheck={false} disabled={needOtp} />
        <label htmlFor="password">Password</label>
        <input id="password" type="password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} required maxLength={256} disabled={needOtp} />
        {needOtp && (
          <>
            <label htmlFor="otp">Authentication code</label>
            <input id="otp" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" value={otp}
              onChange={(e) => setOtp(e.target.value)} required autoFocus placeholder="6-digit code" />
            <p className="muted small">Open your authenticator app and enter the current code for Misinformation Monitor.</p>
          </>
        )}
        {error && <ErrorBox error={error} />}
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Signing in…' : needOtp ? 'Verify' : 'Sign in'}</button>
        {needOtp && (
          <button type="button" className="btn btn-ghost" onClick={() => { setNeedOtp(false); setOtp(''); }}>Use a different account</button>
        )}
      </form>
    </div>
  );
}
