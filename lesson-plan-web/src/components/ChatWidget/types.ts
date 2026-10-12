import type { ProgressStep } from './AgentProgress';
import type { ChatAttachment, GeneratedFileRef } from '@/lib/aiChatApi';

/** 用户上传进对话的资料：与后端 `AttachmentRef` 同形，发回助手时原样带上，不必再映射一次 */
export type MessageAttachment = ChatAttachment;

/** 助手产出的文件卡片：实时 SSE 的 file 事件与历史消息的 generatedFiles 都用它 */
export type GeneratedFile = GeneratedFileRef;

export interface Message {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  createdAt?: string;
  attachments?: MessageAttachment[];
  files?: GeneratedFile[];
  /** 本轮请求的思考/工具调用步骤，渲染在助手气泡上方，可折叠展开 */
  steps?: ProgressStep[];
}

export interface Conversation {
  id: number;
  userId: number;
  title: string;
  /** 会话归属的教案；列表页的「生成文件」靠它恢复到这篇教案的最近对话 */
  lessonPlanId?: number | null;
  createdAt: string;
  updatedAt: string;
}

export interface AiConfig {
  id: number;
  provider: string;
  modelName: string;
  baseUrl: string;
  apiKey: string;
  maxTokens: number;
  temperature: number;
  isActive: boolean;
  systemPrompt: string;
  updatedAt: string;
}
