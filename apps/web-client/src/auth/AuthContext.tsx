import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, login as apiLogin, logout as apiLogout, onSessionChange, refreshSession } from '../api/client';
import type { Me, Role } from '../api/types';

type Status = 'loading' | 'anonymous' | 'authenticated';

interface AuthValue {
  status: Status;
  me: Me | null;
  login: (username: string, password: string, otp?: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
  hasRole: (...roles: Role[]) => boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading');
  const [me, setMe] = useState<Me | null>(null);

  const reload = useCallback(async () => {
    try {
      setMe(await api<Me>('/api/auth/me'));
      setStatus('authenticated');
    } catch {
      setMe(null);
      setStatus('anonymous');
    }
  }, []);

  useEffect(() => {
    // A session ending anywhere (expiry, revocation, sign-out in this tab)
    // drops the user back to anonymous.
    const off = onSessionChange((session) => {
      if (!session) {
        setMe(null);
        setStatus('anonymous');
      }
    });
    // Restore a session from the refresh cookie on first load.
    void refreshSession().then((session) => (session ? reload() : setStatus('anonymous')));
    return () => { off(); };
  }, [reload]);

  const login = useCallback(async (username: string, password: string, otp?: string) => {
    await apiLogin(username, password, otp);
    await reload();
  }, [reload]);

  const logout = useCallback(async () => {
    await apiLogout();
  }, []);

  const value = useMemo<AuthValue>(() => ({
    status, me, login, logout, reload,
    hasRole: (...roles: Role[]) => !!me && roles.includes(me.role),
  }), [status, me, login, logout, reload]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth outside AuthProvider');
  return ctx;
}
