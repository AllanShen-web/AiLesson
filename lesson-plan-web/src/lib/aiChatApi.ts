import imageCompression from 'browser-image-compression';
import { api } from './api';
import { API_BASE } from './env';

/**
 * 上传前压图片体积：手机随手一拍就是 3~8MB，一次传 5 张要等很久，
 * 而视觉模型读课本照片 1600px 长边足够。压完反而更大（少见，如小尺寸 png）就退回原件。
 */
async function compressImageForUpload(file: File): Promise<File> {
  if (!file.type.startsWith('image/') || file.size <= 350 * 1024) return file;
  try {
    const out = await imageCompression(file, {
      maxSizeMB: 0.35,
      maxWidthOrHeight: 1600,
      useWebWorker: true,
    });
    if (out.size >= file.size) return file;
    // 压缩会把 png 转码成 jpeg，文件名跟着改后缀，否则资料库里的扩展名与实际格式对不上
    const name = out.type !== file.type ? file.name.replace(/\.[^.]+$/, '') + '.jpg' : file.name;
    return new File([out], name, { type: out.type });
  } catch {
    return file;
  }
}

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return sessionStorage.getItem('token');
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

export async function streamChat(
  message: string,
  conversationId: number | null,
  onToken: (token: string) => void,
  onDone: (messageId: number, conversationId: number) => void,
  onError: (error: string) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE}/ai-chat/stream`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ message, conversationId }),
  });

  if (!response.ok) {
    onError(`请求失败: ${response.status}`);
    return;
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || !trimmed.startsWith('data: ')) continue;
      try {
        const payload = JSON.parse(trimmed.slice(6));
        if (payload.type === 'token') {
          onToken(payload.content);
        } else if (payload.type === 'done') {
          onDone(payload.messageId, payload.conversationId);
        } else if (payload.type === 'error') {
          onError(payload.content);
        }
      } catch {}
    }
  }
}

export interface ChatAttachment {
  id: number;
  name: string;
  mimetype: string;
  sizeBytes: number;
}

export async function uploadChatFile(file: File): Promise<ChatAttachment> {
  const formData = new FormData();
  formData.append('file', await compressImageForUpload(file));
  const token = sessionStorage.getItem('token');
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/ai-chat/upload`, {
    method: 'POST',
    headers,
    body: formData,
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.message || '上传失败');
  return json.data || json;
}

export interface AgentField {
  id: string;
  label: string;
  type: string;
  /** 模板宽度：1 = 全宽，0.75 = 3/4，0.5 = 半宽，0.25 = 1/4；决定导出文件的排版 */
  width?: number;
  /** 标签位置：left（缺省）与内容并排，top 单独一行在内容上方，none 不显示标签；导出跟随 */
  labelPosition?: 'left' | 'top' | 'none';
  /** 多行文本框高度（行）；导出时决定该字段的最小高度 */
  rows?: number;
  /** 课时分组名（如「第二课时」）：两课时模板的同名字段靠它分清属于哪一栏，单课时模板无此项 */
  group?: string;
}

/** 通用工具确认（Human-in-the-Loop）卡片数据 */
export interface ConfirmationPayload {
  confirmationId: number;
  toolName: string;
  riskLevel: 'low' | 'medium' | 'high';
  title: string;
  summary: string;
  /** 场景化数据（前端按 toolName 渲染，如 PPT 大纲在 data.outline） */
  data: Record<string, any>;
}

/** askChoice 推来的选项卡片：一次可含多问，前端逐问显示，用户点完最后一问把所有选中的文字拼成一条消息发回 */
export interface ChoicePayload {
  questions: { question: string; options: { label: string; description?: string }[] }[];
}

export async function agentChat(
  message: string,
  selectedFieldId: string | null,
  availableFields: AgentField[],
  formContext: Record<string, string>,
  lessonPlanContent: Record<string, string>,
  attachments: ChatAttachment[],
  callbacks: {
    onThinking?: (content: string) => void;
    onToolCall?: (tool: string, args: any) => void;
    /** navigate 类业务事件带 payload（如目标 url），按 toolType 取用 */
    onTool?: (toolType: string, fieldId?: string, content?: string, payload?: Record<string, any>) => void;
    onToolResult?: (tool: string, result: string) => void;
    onToken?: (token: string, accumulated: string) => void;
    onFile?: (file: { fileId: number; fileName: string; format: string; sizeBytes: number }) => void;
    onConfirmation?: (data: ConfirmationPayload) => void;
    onChoice?: (data: ChoicePayload) => void;
    onPptSlide?: (data: { pptId: number; index: number; slide: any }) => void;
    onPptDone?: (data: { pptId: number; slideCount: number }) => void;
    /** 模型给出的下一步引导（已由后端按候选白名单校验，点击可直接作为消息发出） */
    onSuggestions?: (items: string[]) => void;
    onDone?: (data: { conversationId?: number }) => void;
    onError?: (error: string) => void;
  },
  conversationId?: number | null,
  lessonPlanId?: number | null,
  pptId?: number | null,
  confirmation?: { confirmationId: number; decision: 'approved' | 'rejected'; feedback?: string } | null,
  /** 本条消息是否允许助手联网搜索（输入区「联网」开关） */
  webSearch?: boolean,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/ai-chat/agent`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ message, selectedFieldId, availableFields, formContext, lessonPlanContent, attachments, conversationId, lessonPlanId, pptId, confirmation, webSearch }),
    signal,
  });

  if (!response.ok) {
    callbacks.onError?.(`请求失败: ${response.status}`);
    return;
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let accumulated = '';
  let doneReceived = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;
        try {
          const payload = JSON.parse(trimmed.slice(6));
          if (payload.type === 'thinking') {
            callbacks.onThinking?.(payload.content);
          } else if (payload.type === 'toolCall') {
            callbacks.onToolCall?.(payload.tool, payload.args);
          } else if (payload.type === 'tool') {
            callbacks.onTool?.(payload.toolType, payload.fieldId, payload.content, payload);
          } else if (payload.type === 'toolResult') {
            callbacks.onToolResult?.(payload.tool, payload.result);
          } else if (payload.type === 'token') {
            accumulated += payload.content;
            callbacks.onToken?.(payload.content, accumulated);
          } else if (payload.type === 'file') {
            callbacks.onFile?.({ fileId: payload.fileId, fileName: payload.fileName, format: payload.format, sizeBytes: payload.sizeBytes });
          } else if (payload.type === 'confirmation') {
            callbacks.onConfirmation?.({ confirmationId: payload.confirmationId, toolName: payload.toolName, riskLevel: payload.riskLevel, title: payload.title, summary: payload.summary, data: payload.data });
          } else if (payload.type === 'choice') {
            callbacks.onChoice?.({ questions: Array.isArray(payload.questions) ? payload.questions : [] });
          } else if (payload.type === 'pptSlide') {
            callbacks.onPptSlide?.({ pptId: payload.pptId, index: payload.index, slide: payload.slide });
          } else if (payload.type === 'pptDone') {
            callbacks.onPptDone?.({ pptId: payload.pptId, slideCount: payload.slideCount });
          } else if (payload.type === 'suggestions') {
            callbacks.onSuggestions?.(Array.isArray(payload.items) ? payload.items : []);
          } else if (payload.type === 'done') {
            doneReceived = true;
            callbacks.onDone?.({ conversationId: payload.conversationId });
          } else if (payload.type === 'error') {
            doneReceived = true;
            callbacks.onError?.(payload.content);
          }
        } catch {}
      }
    }
  } catch (err: any) {
    // 用户主动停止时 fetch/reader 抛 AbortError：不是故障，不报错，
    // 让下方 onDone 兜底把已流出的半截正文正常收尾
    if (err?.name !== 'AbortError' && !doneReceived) {
      callbacks.onError?.(err.message || '连接中断');
      doneReceived = true;
    }
  }

  // Fallback: if stream ended without done/error event, trigger onDone to reset state
  if (!doneReceived) {
    callbacks.onDone?.({});
  }
}

/** 会话在传输层的形状，与后端 ConversationView 对应 */
export interface AiConversation {
  id: number;
  title: string;
  lessonPlanId: number | null;
  createdAt: string;
  updatedAt: string;
}

/** 助手产出的文件：实时 SSE 的 file 事件和历史消息里的 generatedFiles 同一个形状 */
export interface GeneratedFileRef {
  fileId: number;
  fileName: string;
  format: string;
  sizeBytes: number;
}

/** 折叠面板回看用的步骤；kind 由后端命名，前端只按它挑图标 */
export interface MessageStep {
  kind: string;
  label: string;
  pending?: boolean;
  failed?: boolean;
}

/** 会话内的一条历史消息，与后端 MessageView 对应 */
export interface AiMessage {
  id: number;
  conversationId: number;
  role: 'user' | 'assistant';
  content: string;
  attachments?: ChatAttachment[];
  generatedFiles?: GeneratedFileRef[];
  steps?: MessageStep[];
  createdAt: string;
}

export function getConversations() {
  return api.get<AiConversation[]>('/ai-chat/conversations');
}

export function deleteConversation(id: number) {
  return api.del<{ id: number }>(`/ai-chat/conversations/${id}`);
}

export function getMessages(conversationId: number) {
  return api.get<AiMessage[]>(`/ai-chat/conversations/${conversationId}/messages`);
}

/** 助手配置：管理页专用，后端按 `ai_config:view` 权限放行 */
export function getAiConfig() {
  return api.get<Record<string, any> | null>('/ai-chat/config');
}

export function updateAiConfig(config: Record<string, any>) {
  return api.put<Record<string, any>>('/ai-chat/config', config);
}

export function testAiConnection() {
  return api.post<{ success: boolean; message: string }>('/ai-chat/config/test');
}

/** 二进制接口自己带令牌：`api` 的封装只解析 JSON 外壳 */
async function fetchBinary(path: string, failMessage: string): Promise<Response> {
  const token = typeof window !== 'undefined' ? sessionStorage.getItem('token') : null;
  const res = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(failMessage);
  return res;
}

async function saveBlob(res: Response, fileName: string): Promise<void> {
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
}

const EXPORT_EXT: Record<string, string> = { docx: 'docx', pdf: 'pdf', pptx: 'pptx', md: 'md', txt: 'txt' };

export async function exportLessonPlan(lessonPlanId: number, format: string): Promise<void> {
  const res = await fetchBinary(
    `/ai-chat/export?lessonPlanId=${lessonPlanId}&format=${encodeURIComponent(format)}`,
    '导出失败',
  );
  await saveBlob(res, `教案.${EXPORT_EXT[format] ?? 'txt'}`);
}

export async function downloadAiFile(fileId: number, fileName: string): Promise<void> {
  await saveBlob(await fetchBinary(`/ai-chat/files/${fileId}/download`, '下载失败'), fileName);
}

export function deleteAiFile(fileId: number) {
  return api.del<{ id: number }>(`/ai-chat/files/${fileId}`);
}

/** 资料库用：列出当前用户 AI 生成的文件（不含二进制内容） */
export function listMyAiFiles() {
  return api.get<AiFileItem[]>('/ai-chat/files');
}

export interface AiFileItem {
  id: number;
  fileName: string;
  mimetype: string;
  sizeBytes: number;
  format: string;
  createdAt: string;
}

/** 头像回的是图片本身，且后端不要求鉴权，所以能直接写进 `<img src>` */
export function getAvatarUrl(): string {
  return `${API_BASE}/ai-chat/avatar`;
}

export async function uploadAvatar(file: File): Promise<void> {
  const formData = new FormData();
  formData.append('file', file);
  const token = sessionStorage.getItem('token');
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/ai-chat/avatar`, {
    method: 'POST',
    headers,
    body: formData,
  });
  if (!res.ok) throw new Error('上传头像失败');
}
