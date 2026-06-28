import { api } from './api';
import { API_BASE } from './env';

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
