import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Button, Result, Spin } from 'antd';
import { useTranslation } from 'react-i18next';
import { authApi, clearToken, getToken } from './api';
import LoginPage from './pages/LoginPage';

type GateState = 'checking' | 'open' | 'setup' | 'login' | 'authed' | 'unreachable';

interface AuthContextValue {
  authEnabled: boolean;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue>({
  authEnabled: false,
  logout: () => {},
});

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}

interface AuthGateProps {
  children: ReactNode;
}

export default function AuthGate({ children }: AuthGateProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<GateState>('checking');

  const evaluate = useCallback(async () => {
    setState('checking');
    try {
      const statusRes = await authApi.status();
      const { auth_disabled: authDisabled, needs_setup: needsSetup } = statusRes.data;

      if (authDisabled) {
        setState('open');
        return;
      }

      const token = getToken();
      if (!token) {
        setState(needsSetup ? 'setup' : 'login');
        return;
      }

      try {
        await authApi.me();
        setState('authed');
      } catch {
        clearToken();
        setState(needsSetup ? 'setup' : 'login');
      }
    } catch {
      setState('unreachable');
    }
  }, []);

  useEffect(() => {
    evaluate();
  }, [evaluate]);

  useEffect(() => {
    const handleUnauthorized = () => setState('login');
    window.addEventListener('aezab:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('aezab:unauthorized', handleUnauthorized);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setState('login');
  }, []);

  if (state === 'checking') {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (state === 'unreachable') {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Result
          status="warning"
          title={t('auth.serverUnreachable')}
          extra={
            <Button type="primary" onClick={evaluate}>
              {t('auth.retry')}
            </Button>
          }
        />
      </div>
    );
  }

  if (state === 'setup') {
    return <LoginPage mode="setup" onSuccess={evaluate} />;
  }

  if (state === 'login') {
    return <LoginPage mode="login" onSuccess={evaluate} />;
  }

  return (
    <AuthContext.Provider value={{ authEnabled: state === 'authed', logout }}>
      {children}
    </AuthContext.Provider>
  );
}
