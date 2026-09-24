import { API_BASE } from './env';

interface ApiResponse<T = unknown> {
  code: number;
  data: T;
  message: string;
}

let globalErrorHandler: ((msg: string) => void) | null = null;
let isRedirecting = false;
// 早期每个页面自己拼 URL 调 fetch，统一封装完先留着对照
// const legacyRequest = (path: string) => fetch(`${API_BASE}${path}`);

export function setGlobalErrorHandler(handler: (msg: string) => void) {
  globalErrorHandler = handler;
}

export function resetRedirecting() {
  isRedirecting = false;
}

/** 列表接口的查询参数：undefined、null 和空串一律不拼上去，让后端按缺省走 */
export type QueryParams = Record<string, string | number | boolean | undefined | null>;

function toQuery(params?: QueryParams): string {
  if (!params) return '';
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
  });
  const query = search.toString();
  return query ? `?${query}` : '';
}

/** 后端异常时的兜底文案：正常情况下 message 由后端给（都是中文），这里只补它没给的部分 */
function httpFallbackMessage(status: number): string {
  if (status === 401) return '登录状态已失效，请重新登录';
  if (status === 403) return '无权限执行此操作';
  if (status === 404) return '请求的内容不存在';
  if (status >= 500) return '服务器繁忙，请稍后重试';
  return '操作失败，请稍后重试';
}

/**
 * 一律先读文本再解析：非 JSON 的响应（网关错误页、被重定向到的登录页）直接
 * res.json() 会抛英文的 "Unexpected token '<'"，用户看到的就是天书。
 */
async function parseResponse<T>(res: Response): Promise<ApiResponse<T>> {
  const text = await res.text();
  if (!text) {
    return {
      code: res.ok ? 0 : res.status,
      data: undefined as T,
      message: res.ok ? 'success' : httpFallbackMessage(res.status),
    };
  }
  try {
    return JSON.parse(text) as ApiResponse<T>;
  } catch {
    return { code: res.status, data: undefined as T, message: httpFallbackMessage(res.status) };
  }
}

class ApiClient {
  private getToken(): string | null {
    if (typeof window === 'undefined') return null;
    return sessionStorage.getItem('token');
  }

  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const token = this.getToken();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string>),
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    let res: Response;
    try {
      res = await fetch(`${API_BASE}${path}`, { ...options, headers });
    } catch {
      // 后端不通、跨域被拦、代理挂了都走这里，fetch 原本抛的是英文 Failed to fetch
      throw new Error('无法连接服务器，请检查网络或稍后重试');
    }

    const json = await parseResponse<T>(res);

    if (!res.ok || json.code !== 0) {
      const msg = json.message || httpFallbackMessage(res.status);
      // 登录/注册这类接口返的 401 是「账号或密码错误」，不是登录态过期，
      // 一律跳 /login 会把登录页自己刷掉，报错提示也就跟着没了
      const isAuthEntry = path.startsWith('/auth/');
      if (res.status === 401 && !isAuthEntry) {
        if (!isRedirecting) {
          isRedirecting = true;
          sessionStorage.removeItem('token');
          // 整页刷新会带走 toast，原因先存一下，由登录页读出后提示
          sessionStorage.setItem('flashMessage', '登录状态已失效，请重新登录');
          if (typeof window !== 'undefined') window.location.href = '/login';
        }
      } else if (res.status === 403) {
        globalErrorHandler?.('无权限执行此操作');
      } else {
        globalErrorHandler?.(msg);
      }
      throw new Error(msg);
    }

    return json.data;
  }

  get<T>(path: string, params?: QueryParams, options?: RequestInit) {
    return this.request<T>(`${path}${toQuery(params)}`, options);
  }

  post<T>(path: string, body?: unknown, options?: RequestInit) {
    return this.request<T>(path, {
      method: 'POST',
      body: JSON.stringify(body),
      ...options,
    });
  }

  put<T>(path: string, body: unknown, options?: RequestInit) {
    return this.request<T>(path, {
      method: 'PUT',
      body: JSON.stringify(body),
      ...options,
    });
  }

  del<T>(path: string, options?: RequestInit) {
    return this.request<T>(path, { method: 'DELETE', ...options });
  }

  patch<T>(path: string, body: unknown, options?: RequestInit) {
    return this.request<T>(path, {
      method: 'PATCH',
      body: JSON.stringify(body),
      ...options,
    });
  }
}

export const api = new ApiClient();

export interface LoginData {
  token: string;
  /** 后端直接回完整档案，前端登录后不必再发一次 /users/me */
  userInfo: UserInfo;
}

export interface UserInfo {
  id: number;
  username: string;
  nickname?: string;
  email?: string;
  roleCode?: string;
  avatar?: string;
  createdAt?: string;
  role?: { id: number; name: string; code: string } | null;
  /**
   * 当前账号生效的权限码，由角色的菜单授权推导出来，前端只用来隐藏入口，
   * 真正的放行与否由后端 PermissionGuard 判定，藏不住也不等于能越权。
   */
  permissions?: string[];
}

/** 受保护操作目录里的一条：挂在某个菜单路径上，勾了该菜单即拥有它 */
export interface PermissionCatalogItem {
  code: string;
  label: string;
  menuPath: string;
}

export interface MenuItem {
  id: number;
  name: string;
  icon: string;
  path: string;
  sort: number;
  parentId: number | null;
  createdAt: string;
}

export const authApi = {
  login: (username: string, password: string) =>
    api.post<LoginData>('/auth/login', { username, password }),
  register: (username: string, password: string, email: string, code: string, nickname?: string) =>
    api.post<{ id: number; username: string; email: string }>('/auth/register', { username, password, email, code, nickname }),
  checkUsername: (username: string) =>
    api.post<{ exists: boolean }>('/auth/check-username', { username }),
  sendCode: (email: string, type: string) =>
    api.post<{ message: string }>('/auth/send-code', { email, type }),
  sendLoginCode: (email: string) =>
    api.post<{ message: string }>('/auth/send-login-code', { email }),
  loginByEmail: (email: string, code: string) =>
    api.post<LoginData>('/auth/login-by-email', { email, code }),
};

export const userApi = {
  getInfo: () => api.get<UserInfo>('/users/me'),
  updateProfile: (data: { username?: string; nickname?: string }) =>
    api.put<UserInfo>('/users/me/profile', data),
  getAvatarHistory: () => api.get<string[]>('/users/me/avatar-history'),
  useAvatarHistory: (avatarUrl: string) =>
    api.put<UserInfo>('/users/me/avatar-history', { avatarUrl }),
  removeAvatarHistory: (index: number) =>
    api.del<string[]>(`/users/me/avatar-history/${index}`),
  sendChangePasswordCode: () =>
    api.post<{ message: string }>('/users/me/change-password/code'),
  changePasswordByCode: (code: string, newPassword: string) =>
    api.put<{ message: string }>('/users/me/password', { code, newPassword }),
  sendNewEmailCode: (newEmail: string) =>
    api.post<{ message: string }>('/users/me/email/code', { newEmail }),
  changeEmail: (newEmail: string, password: string, code: string) =>
    api.put<UserInfo>('/users/me/email', { newEmail, password, code }),
};

export const menuApi = {
  getMenus: () => api.get<MenuItem[]>('/menus'),
  getMyMenus: () => api.get<MenuItem[]>('/menus/mine'),
  create: (data: Partial<MenuItem>) => api.post<MenuItem>('/menus', data),
  update: (id: number, data: Partial<MenuItem>) => api.put<MenuItem>(`/menus/${id}`, data),
  remove: (id: number) => api.del<void>(`/menus/${id}`),
};

export interface RoleItem {
  id: number;
  name: string;
  code: string;
  description: string | null;
  createdAt: string;
  /** 已授权的菜单路径；这个系统的权限就只有这一个来源 */
  menuPaths?: string[];
}

export interface UserListItem {
  id: number;
  username: string;
  nickname: string | null;
  email: string | null;
  roleCode: string | null;
  frozen: boolean;
  createdAt: string;
  role: { id: number; name: string; code: string } | null;
}

export const roleApi = {
  getAll: () => api.get<RoleItem[]>('/roles'),
  create: (data: Partial<RoleItem>) => api.post<RoleItem>('/roles', data),
  update: (id: number, data: Partial<RoleItem>) => api.put<RoleItem>(`/roles/${id}`, data),
  remove: (id: number) => api.del<void>(`/roles/${id}`),
  getMenus: (id: number) => api.get<string[]>(`/roles/${id}/menus`),
  setMenus: (id: number, menuPaths: string[]) => api.put<void>(`/roles/${id}/menus`, { menuPaths }),
  getPermissionCatalog: () => api.get<PermissionCatalogItem[]>('/roles/permission-catalog'),
};

export const userManagementApi = {
  getList: () => api.get<UserListItem[]>('/users'),
  update: (id: number, data: { username?: string; email?: string; nickname?: string; roleCode?: string }) =>
    api.put<UserListItem>(`/users/${id}`, data),
  toggleFrozen: (id: number) => api.put<UserListItem>(`/users/${id}/frozen`),
  uploadAvatar: async (file: File): Promise<{ url: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const token = sessionStorage.getItem('token');
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}/users/me/avatar`, {
      method: 'POST',
      headers,
      body: formData,
    });
    const json = await res.json();
    if (!res.ok || json.code !== 0) throw new Error(json.message || '上传失败');
    return json.data;
  },
};

