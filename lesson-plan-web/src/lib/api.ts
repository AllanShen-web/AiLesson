import { API_BASE } from './env';

interface ApiResponse<T = unknown> {
  code: number;
  data: T;
  message: string;
}

let globalErrorHandler: ((msg: string) => void) | null = null;
let isRedirecting = false;

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
  sendCode: (email: string, purpose: string) =>
    api.post<{ message: string }>('/auth/send-code', { email, purpose }),
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

/** 勾菜单是唯一授权动作，后端回的是过滤后的实际生效值：陈旧路径已被丢掉，前端据此刷新勾选 */
export interface MenuGrantResult {
  roleId: number;
  menuPaths: string[];
}

export const roleApi = {
  getAll: () => api.get<RoleItem[]>('/roles'),
  create: (data: Partial<RoleItem>) => api.post<RoleItem>('/roles', data),
  update: (id: number, data: Partial<RoleItem>) => api.put<RoleItem>(`/roles/${id}`, data),
  remove: (id: number) => api.del<void>(`/roles/${id}`),
  getMenus: (id: number) => api.get<string[]>(`/roles/${id}/menus`),
  setMenus: (id: number, menuPaths: string[]) => api.put<MenuGrantResult>(`/roles/${id}/menus`, { menuPaths }),
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

export interface BlessEntry {
  id: number;
  userId: number;
  nickname: string;
  content: string;
  imageUrl: string | null;
  color: string;
  hidden: boolean;
  createdAt: string;
}

export interface BlessSettings {
  title: string;
  titleColor: string;
  subtitle: string;
  subtitleColor: string;
  blessings: { text: string; color: string; bold: boolean }[];
  footerText: string;
  enableFloating: boolean;
  enableGradient: boolean;
  bgImageUrl: string | null;
  bgImageUrls: string[];
  bgInterval: number;
  replyMessage: string;
  replyBgColor: string;
  replyTextColor: string;
}

export interface BlessSettingRecord {
  id: number;
  userId: number;
  settings: BlessSettings;
  enabled: boolean;
  updatedAt: string;
  username?: string;
  nickname?: string;
}

export const blessApi = {
  getMyEntries: () => api.get<BlessEntry[]>('/blessings/mine'),
  /** 某人的公开祝福墙：墙页是给人看的，不要求登录 */
  getWall: (username: string) =>
    api.get<BlessEntry[]>(`/blessings/users/${encodeURIComponent(username)}`),
  create: (content: string, imageUrl?: string, nickname?: string, tempId?: string, targetUsername?: string) =>
    api.post<BlessEntry>('/blessings', { content, imageUrl: imageUrl || null, nickname, tempId: tempId || null, targetUsername: targetUsername || null }),
  remove: (id: number) => api.del<{ id: number }>(`/blessings/${id}`),
  removeByTempId: (id: number, tempId: string) =>
    api.post<{ id: number }>(`/blessings/${id}/recall`, { tempId }),
  toggleHidden: (id: number) => api.patch<BlessEntry>(`/blessings/${id}/hidden`),
  uploadImage: async (file: File): Promise<{ url: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const token = sessionStorage.getItem('token');
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}/blessings/upload`, {
      method: 'POST',
      headers,
      body: formData,
    });
    const json = await res.json();
    if (!res.ok || json.code !== 0) throw new Error(json.message || '上传失败');
    return json.data;
  },
};

export const blessSettingApi = {
  getMy: () => api.get<BlessSettingRecord>('/blessing-settings/mine'),
  getPublic: (username: string) =>
    api.get<BlessSettingRecord>(`/blessing-settings/users/${encodeURIComponent(username)}`),
  /** 墙面配置服务端不解析，原样存进 JSONB，所以这里直接送对象而不是序列化后的字符串 */
  update: (settings: BlessSettings) =>
    api.put<BlessSettingRecord>('/blessing-settings', { settings }),
  toggleEnabled: () => api.post<BlessSettingRecord>('/blessing-settings/toggle-enabled'),
};

export interface FormField {
  id: string;
  /**
   * label = 纯栏目标题：只有标题、没有输入控件，用于模板里的标题套标题；
   * divider = 分隔符：没有内容的整行断点，把它上下两侧的字段各成一张独立的表，
   *           连续两个分隔符则在断开表格之外再真换页（对应 Word 里的分页符）
   */
  type: 'input' | 'textarea' | 'select' | 'number' | 'label' | 'divider';
  label: string;
  placeholder?: string;
  options?: string[];
  width?: number; // 1/12 递增十二档：1/12 ~ 1
  labelPosition?: 'left' | 'top' | 'none'; // 缺省 left：label 与内容左右并排；none 只留内容、不显示标签
  rows?: number; // 高度（行）：多行文本与纯栏目标题共用，缺省 4
  rowSpan?: number; // 纵向贯穿行数（仅纯栏目标题）：Word 里的竖排合并大标题，缺省 1
}

export interface FormSchema {
  fields: FormField[];
}

/**
 * 基础信息四项的固定 id：自动回填、助手上下文、导出字段名解析都靠它认字段，
 * 模板里换了别的 id 就享受不到这些行为。
 */
export const BASIC_FIELD_IDS = ['basic-topic', 'basic-unit', 'basic-subject', 'basic-grade'] as const;

export function isBasicField(id: string): boolean {
  return (BASIC_FIELD_IDS as readonly string[]).includes(id);
}

/**
 * 缺省按半宽（2×2）处理的历史字段 id：模板编辑页的 basic-* 与官方模板的 official-*。
 * 老模板没写 width，靠这份名单保住原来的两列排版；后端 `templates/schemas.py` 的 `HALF_WIDTH_DEFAULT_IDS`
 * 是同一份口径，改这里要同步。
 */
const HALF_WIDTH_DEFAULT_IDS: readonly string[] = [
  ...BASIC_FIELD_IDS,
  'official-subject',
  'official-grade',
  'official-textbook',
  'official-title',
];

/** 字段缺省宽度：显式调过滑杆的用滑杆值，没调过的按历史口径兜底 */
export function fieldWidth(field: { id: string; width?: number }): number {
  return field.width ?? (HALF_WIDTH_DEFAULT_IDS.includes(field.id) ? 0.5 : 1);
}

/** label 是否在顶部（缺省在左）；填写页、详情弹窗、导出文件都按这个口径排版 */
export function isTopLabel(field: { labelPosition?: string }): boolean {
  return field.labelPosition === 'top';
}

/** 无标签：这一格只留内容，画布、填写页、详情弹窗、导出文件都不显示标签文字（字段本身仍可填写、可被 AI 生成） */
export function isNoLabel(field: { labelPosition?: string }): boolean {
  return field.labelPosition === 'none';
}

/** 标签是否不占内容左侧（在上或无标签）：这两种版式下控件通栏，富文本按钮组也放到框上方 */
export function isStackedLabel(field: { labelPosition?: string }): boolean {
  return isTopLabel(field) || isNoLabel(field);
}

/**
 * 纯栏目标题按导出口径折行：每 1/12 宽度放一个全角字（1/12 正好一字一行，竖排标题效果），
 * 与后端 `ai_chat/files/layout.py` 的标题格折行一致，页面和导出文件看到的是同一个排法。
 */
export function splitLabelLines(text: string, width: number): string[] {
  const perLine = Math.max(1, Math.round(width * 12));
  const chars = Array.from(text);
  const lines: string[] = [];
  for (let i = 0; i < chars.length; i += perLine) lines.push(chars.slice(i, i + perLine).join(''));
  return lines;
}

/** 一行的像素高：模板里多行文本字段的高度按行给，缺省 4 行约等于改版前的 96px 框 */
const PREVIEW_BOX_HEIGHT = 22;

/**
 * 多行文本展示框的高度：模板设定的行数 × 行高，带内框时再加上下各 8px 内边距
 * （bare 模式的外框由调用方画，内边距也算在内，所以不加）。
 * 填写页/详情弹窗的展示框和模板编辑画布上的字段条都按这个高度来，改这里三处一起变。
 */
export function previewBoxHeight(rows: number | undefined, bare: boolean): number {
  return (rows ?? 4) * PREVIEW_BOX_HEIGHT + (bare ? 0 : 16);
}

export interface FieldGridCell<T> {
  field: T;
  /** 占几列（12 等分） */
  colSpan: number;
  /** 纵向贯穿几行（仅纯栏目标题 >1） */
  rowSpan: number;
}

export interface FieldGridRow<T> {
  /** 行首被上方贯穿标题占掉的列数（12 等分），本行单元格从剩余列开始 */
  covered: number;
  cells: FieldGridCell<T>[];
}

/**
 * 按模板顺序把字段摆成 12 列的表格网格，画布、教案填写页、详情弹窗、导出文件共用这一套口径
 * （后端 `ai_chat/files/layout.py` 的 `build_layout` 是同一算法，改这里要同步）。
 * - 宽度累加超过 12 列换行
 * - 纯栏目标题带 rowSpan>1（Word 里的竖排合并大标题）时站行首：多个竖排标题可共用同一行行首，
 *   紧跟其后的整宽条目收缩到剩余列、与标题头部同行顶格；它向下贯穿的几行行首预留它的列宽
 */
export function layoutFieldGrid<T extends { id: string; width?: number; type?: string; rowSpan?: number }>(
  fields: T[],
): FieldGridRow<T>[] {
  const rowSpanOf = (f: T): number =>
    f.type === 'label' && typeof f.rowSpan === 'number' && f.rowSpan >= 2 && f.rowSpan <= 12
      ? Math.round(f.rowSpan)
      : 1;

  const rows: FieldGridRow<T>[] = [];
  let pending: { span: number; rowsLeft: number }[] = [];
  let cur: FieldGridCell<T>[] = [];
  let covered = 0;
  let used = 0;
  let open = false;

  const flush = () => {
    if (cur.length > 0) rows.push({ covered, cells: cur });
    cur = [];
    covered = 0;
    used = 0;
  };
  const openRow = () => {
    flush();
    pending = pending.filter((p) => p.rowsLeft > 0);
    covered = 0;
    for (const p of pending) {
      covered += p.span;
      p.rowsLeft -= 1;
    }
    used = covered;
    open = true;
  };

  for (const field of fields) {
    if (!open) openRow();
    const rowSpan = rowSpanOf(field);
    let colSpan = Math.max(1, Math.round(fieldWidth(field) * 12));
    // 贯穿标题必须站行首：本行已有普通条目就另起一行；只有其他贯穿标题时接着并排往右放
    // （原表格里多个竖排大标题共用同一行行首，各起一行会在上面空出整行轨道）
    let onlySpans = cur.every((c) => c.rowSpan > 1);
    if (rowSpan > 1 && !onlySpans) {
      openRow();
      onlySpans = true;
    }
    if (used + colSpan > 12 && !onlySpans) {
      openRow();
      onlySpans = true;
    }
    // 行首只有贯穿标题时，剩余列放不下也排进去（顶格与标题同行），收缩到剩余列
    if (onlySpans && used + colSpan > 12) colSpan = Math.max(1, 12 - used);
    cur.push({ field, colSpan, rowSpan });
    used += colSpan;
    if (rowSpan > 1) pending.push({ span: colSpan, rowsLeft: rowSpan - 1 });
  }
  flush();
  return rows;
}

export interface FieldSegment<T> {
  fields: T[];
  /** 这一段前面连着几个分隔符（首段恒为 0）：页面上按个数画几条横线 */
  dividerCount: number;
  /** 这一段落前面是否带一次真换页（连续 ≥2 个分隔符）；首段恒为 false */
  pageBreak: boolean;
}

/**
 * 按分隔符把字段切成若干段，每段是一张独立的表（画布、填写页、详情页、导出共用这一份口径，
 * 后端 `ai_chat/files/layout.py` 的 `split_layout_fields` 是同一算法，改这里要同步；
 * 后端只需要是否换页，所以 dividerCount 只有前端用）。
 * 连续 ≥2 个分隔符算一条「换页」边界；前导与尾随的分隔符后面没有内容可切，不产生空段。
 */
export function splitByDividers<T extends { type?: string }>(fields: T[]): FieldSegment<T>[] {
  const segments: FieldSegment<T>[] = [];
  let run = 0;
  for (const field of fields) {
    if (field.type === 'divider') {
      run += 1;
      continue;
    }
    if (segments.length === 0) {
      segments.push({ fields: [field], dividerCount: 0, pageBreak: false });
    } else if (run > 0) {
      segments.push({ fields: [field], dividerCount: run, pageBreak: run >= 2 });
    } else {
      segments[segments.length - 1].fields.push(field);
    }
    run = 0;
  }
  return segments;
}

export interface TemplateItem {
  id: number;
  name: string;
  userId: number;
  creatorName: string;
  isOfficial: boolean;
  isPublic: boolean;
  content: FormSchema;
  createdAt: string;
}

export const templateApi = {
  getMy: (options?: RequestInit) => api.get<TemplateItem[]>('/templates', undefined, options),
  getPublic: (options?: RequestInit) => api.get<TemplateItem[]>('/templates/public', undefined, options),
  getById: (id: number, options?: RequestInit) => api.get<TemplateItem>(`/templates/${id}`, undefined, options),
  /**
   * 上传 Word 识别成模板草稿（只返回，不落库；确认后走 create）。
   * 直接 fetch 而不是 api.post：封装固定带 Content-Type: application/json，
   * multipart 的 boundary 必须由浏览器自己填。
   */
  importDocx: async (file: File): Promise<{ name: string; fields: FormField[] }> => {
    const formData = new FormData();
    formData.append('file', file);
    const token = typeof window !== 'undefined' ? sessionStorage.getItem('token') : null;
    const res = await fetch(`${API_BASE}/templates/import-docx`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: formData,
    });
    const json = await res.json();
    if (!res.ok || json.code !== 0) throw new Error(json.message || '导入失败');
    return json.data;
  },
  create: (data: { name: string; content: FormSchema }) =>
    api.post<TemplateItem>('/templates', data),
  update: (id: number, data: { name: string; content: FormSchema }) =>
    api.put<TemplateItem>(`/templates/${id}`, data),
  remove: (id: number) =>
    api.del<{ id: number }>(`/templates/${id}`),
  togglePublic: (id: number) =>
    api.patch<TemplateItem>(`/templates/${id}/public`),
  copy: (id: number) =>
    api.post<TemplateItem>(`/templates/${id}/copy`),
};

export interface ResourceItem {
  id: number;
  name: string;
  subject: string;
  grade: string;
  isPublic: boolean;
  originalName: string;
  mimetype: string;
  sizeBytes: number;
  userId: number;
  creatorName: string;
  createdAt: string;
}

export const resourceApi = {
  getMy: () => api.get<ResourceItem[]>('/resources'),
  getPublic: () => api.get<ResourceItem[]>('/resources/public'),
  getById: (id: number) => api.get<ResourceItem>(`/resources/${id}`),
  upload: async (formData: FormData): Promise<ResourceItem> => {
    const token = sessionStorage.getItem('token');
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}/resources/upload`, {
      method: 'POST',
      headers,
      body: formData,
    });
    const json = await res.json();
    if (!res.ok || json.code !== 0) throw new Error(json.message || '上传失败');
    return json.data;
  },
  update: (id: number, data: { name?: string; subject?: string; grade?: string; isPublic?: boolean }) =>
    api.put<ResourceItem>(`/resources/${id}`, data),
  remove: (id: number) =>
    api.del<{ id: number }>(`/resources/${id}`),
  togglePublic: (id: number) =>
    api.patch<ResourceItem>(`/resources/${id}/public`),
  getPreviewUrl: (id: number) => `${API_BASE}/resources/${id}/preview`,
  getDownloadUrl: (id: number) => `${API_BASE}/resources/${id}/download`,
};

export interface RagDocumentStatus {
  resourceId: number;
  status: 'pending' | 'processing' | 'ready' | 'failed';
  chunkCount: number;
  errorMessage: string | null;
}

export interface RagPreview {
  charCount: number;
  chunkCount: number;
  text: string;
}

export const ragApi = {
  preview: (resourceId: number) => api.post<RagPreview>('/rag/documents/preview', { resourceId }),
  vectorize: (resourceId: number, text: string) => api.post<{ status: string }>('/rag/documents', { resourceId, text }),
  statuses: (resourceIds: number[]) =>
    api.get<RagDocumentStatus[]>('/rag/documents/status', { resourceIds: resourceIds.join(',') }),
  clear: (resourceId: number) => api.del<{ success: boolean }>(`/rag/documents/${resourceId}`),
};

export interface LessonPlanContent {
  fields: FormField[];
  values: Record<string, string | number>;
}

export interface LessonPlanItem {
  id: number;
  title: string;
  subject: string;
  grade: string;
  templateId: number;
  content: LessonPlanContent;
  isPublic: boolean;
  userId: number;
  creatorName: string;
  createdAt: string;
  updatedAt: string;
}

export interface LessonPlanPageResult {
  items: LessonPlanItem[];
  total: number;
  page: number;
  pageSize: number;
}

/** 用 `type` 而不是 `interface`：`api.get` 的查询参数是 `Record<string, …>` 形状，
 *  而 TS 只给类型别名隐式索引签名，interface 赋不进去。 */
export type LessonPlanQuery = {
  subject?: string;
  grade?: string;
  /** 只看已公开 / 未公开；公共库有自己的路由，不靠这个开关 */
  isPublic?: boolean;
  keyword?: string;
  sortField?: 'createdAt' | 'updatedAt';
  sortOrder?: 'asc' | 'desc';
  page?: number;
  pageSize?: number;
};

export const lessonPlanApi = {
  getMy: (query?: LessonPlanQuery, options?: RequestInit) =>
    api.get<LessonPlanPageResult>('/lesson-plans', query, options),
  getMyAll: (query?: { subject?: string; grade?: string; keyword?: string }, options?: RequestInit) =>
    api.get<LessonPlanItem[]>('/lesson-plans/all', query, options),
  getPublic: (query?: LessonPlanQuery, options?: RequestInit) =>
    api.get<LessonPlanPageResult>('/lesson-plans/public', query, options),
  getPublicAll: (query?: { subject?: string; grade?: string; keyword?: string }, options?: RequestInit) =>
    api.get<LessonPlanItem[]>('/lesson-plans/public/all', query, options),
  getById: (id: number) => api.get<LessonPlanItem>(`/lesson-plans/${id}`),
  create: (data: { title: string; subject?: string; grade?: string; templateId?: number; content?: LessonPlanContent; isPublic?: boolean }) =>
    api.post<LessonPlanItem>('/lesson-plans', data),
  update: (id: number, data: { title?: string; subject?: string; grade?: string; content?: LessonPlanContent; isPublic?: boolean }) =>
    api.put<LessonPlanItem>(`/lesson-plans/${id}`, data),
  remove: (id: number) => api.del<{ id: number }>(`/lesson-plans/${id}`),
  copy: (id: number) => api.post<LessonPlanItem>(`/lesson-plans/${id}/copy`),
  togglePublic: (id: number) => api.patch<LessonPlanItem>(`/lesson-plans/${id}/public`),
  /** 按课题名推断单元/学科/年级；模型不确定的字段返回空串 */
  predictBasic: (body: { topic: string; options: { subject: string[]; grade: string[]; unit: string[] } }) =>
    api.post<{ subject: string; grade: string; unit: string }>('/lesson-plans/predict-basic', body),
};
