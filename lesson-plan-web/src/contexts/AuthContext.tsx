'use client';

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from 'react';
import { useRouter } from 'next/navigation';
import { message } from 'antd';
import { authApi, userApi, menuApi, resetRedirecting, type UserInfo } from '@/lib/api';
import { ROUTES } from '@/constants/routes';

interface AuthContextType {
  user: UserInfo | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  loginByEmail: (email: string, code: string) => Promise<void>;
  register: (username: string, password: string, email: string, code: string, nickname?: string) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
  hasPermission: (code: string) => boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const loadUser = useCallback(async () => {
    const token = sessionStorage.getItem('token');
    if (!token) {
      setLoading(false);
      return;
    }
    try {
      const info = await userApi.getInfo();
      setUser(info);
    } catch {
      sessionStorage.removeItem('token');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadUser();
  }, [loadUser]);

  const redirectToFirstMenu = async () => {
    try {
      const menus = await menuApi.getMyMenus();
      if (menus.length === 0) {
        // 角色没勾任何菜单时以前会静默落到首页，现在首页已删，必须说清楚为什么侧边栏是空的
        message.warning('当前账号还没有分配任何菜单，请联系管理员');
      }
      router.push(menus[0]?.path ?? ROUTES.LESSON_PLANS);
    } catch {
      message.error('菜单加载失败，部分功能可能无法使用');
      router.push(ROUTES.LESSON_PLANS);
    }
  };

  const login = async (username: string, password: string) => {
    const data = await authApi.login(username, password);
    sessionStorage.setItem('token', data.token);
    resetRedirecting();
    setUser(data.userInfo);
    await redirectToFirstMenu();
  };

  const loginByEmail = async (email: string, code: string) => {
    const data = await authApi.loginByEmail(email, code);
    sessionStorage.setItem('token', data.token);
    resetRedirecting();
    setUser(data.userInfo);
    await redirectToFirstMenu();
  };

  const register = async (username: string, password: string, email: string, code: string, nickname?: string) => {
    await authApi.register(username, password, email, code, nickname);
  };

  const logout = () => {
    sessionStorage.removeItem('token');
    setUser(null);
    router.push(ROUTES.LOGIN);
  };

  const hasPermission = useCallback((code: string) => !!user?.permissions?.includes(code), [user]);

  return (
    <AuthContext.Provider
      value={{ user, loading, login, loginByEmail, register, logout, refreshUser: loadUser, hasPermission }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
