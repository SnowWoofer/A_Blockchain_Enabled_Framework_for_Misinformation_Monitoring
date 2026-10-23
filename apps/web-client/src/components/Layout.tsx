import { useEffect, useState, type ReactNode } from 'react';
import { Link, NavLink, Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { PublicInfo, Role } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { ROLE_LABEL, orgName } from '../lib/format';
import { Loading } from './ui';

function DemoBanner() {
  const [info, setInfo] = useState<PublicInfo>();
  useEffect(() => { api<PublicInfo>('/api/public/info').then(setInfo).catch(() => undefined); }, []);
  if (info?.ledger_backend !== 'mock') return null;
  return (
    <div className="demo-banner" role="note">
      Demo mode: votes are kept in an in-memory test ledger, not on the Hyperledger Fabric blockchain.
    </div>
  );
}

function AccountMenu() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  if (!me) return <Link className="btn btn-secondary btn-sm" to="/login">Fact-checker sign in</Link>;
  return (
    <div className="account">
      <Link to="/account" className="account-who">
        <span className="account-name">{me.user?.display_name || me.subject}</span>
        <span className="account-meta">{ROLE_LABEL[me.role]} · {orgName(me.mspid)}</span>
      </Link>
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() => { void logout().then(() => navigate('/')); }}
      >
        Sign out
      </button>
    </div>
  );
}

export function Layout() {
  const { me, hasRole } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMenuOpen(false), [location.pathname]);
  const staff = hasRole('official', 'fact_checker', 'observer');
  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <DemoBanner />
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand">
            <img src="/favicon.svg" alt="" width="28" height="28" />
            <span>Misinformation Monitor</span>
          </Link>
          <button type="button" className="menu-toggle btn btn-ghost btn-sm" aria-expanded={menuOpen}
            aria-controls="primary-nav" onClick={() => setMenuOpen((o) => !o)}>Menu</button>
          <nav id="primary-nav" className={`nav${menuOpen ? ' nav-open' : ''}`} aria-label="Primary">
            <NavLink to="/verdicts">Verdicts</NavLink>
            <NavLink to="/submit">Report a claim</NavLink>
            <NavLink to="/track">Track a report</NavLink>
            {staff && <NavLink to="/workspace">Review queue</NavLink>}
            {hasRole('official') && <NavLink to="/admin/governance">Console</NavLink>}
          </nav>
          <AccountMenu />
        </div>
      </header>
      <main id="main" className="main">
        {me?.must_change_password && location.pathname !== '/account' ? <Navigate to="/account" replace /> : <Outlet />}
      </main>
      <footer className="footer">
        <p>
          Claims are screened by an AI model and decided by an independent consortium of fact-checking
          organisations. A claim is confirmed as misinformation only when two-thirds of member organisations agree,
          and every vote is recorded on a Hyperledger Fabric blockchain.
        </p>
      </footer>
    </div>
  );
}

export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { status, me } = useAuth();
  const location = useLocation();
  if (status === 'loading') return <Loading label="Checking your session…" />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (!roles.includes(me.role)) {
    return (
      <div className="page narrow">
        <h1>Not available for your role</h1>
        <p>Your account ({ROLE_LABEL[me.role]}) cannot open this page. Ask an official in your organisation if you need access.</p>
      </div>
    );
  }
  return <>{children}</>;
}

export function ConsoleNav() {
  return (
    <nav className="subnav" aria-label="Console">
      <NavLink to="/admin/governance">Governance</NavLink>
      <NavLink to="/admin/consortium">Consortium</NavLink>
      <NavLink to="/admin/users">Users</NavLink>
      <NavLink to="/admin/audit">Audit log</NavLink>
    </nav>
  );
}

export function WorkspaceNav() {
  return (
    <nav className="subnav" aria-label="Workspace">
      <NavLink to="/workspace" end>To review</NavLink>
      <NavLink to="/workspace/reviewed">Reviewed by my organisation</NavLink>
    </nav>
  );
}
