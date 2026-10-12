'use client';

import { useState } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { CopyOutlined, CheckOutlined, FileOutlined, DownloadOutlined, EyeOutlined, CloseOutlined, DeleteOutlined } from '@ant-design/icons';
import { Button, Popconfirm, message as antdMessage } from 'antd';
import type { GeneratedFile, Message } from './types';
import { downloadAiFile, deleteAiFile } from '@/lib/aiChatApi';
import { API_BASE, API_HOST } from '@/lib/env';
import AiAvatar from './AiAvatar';
import AgentProgress from './AgentProgress';
import FilePreview from '@/components/FilePreview';
import type { PreviewSource } from '@/components/FilePreview/types';
import { useAuth } from '@/contexts/AuthContext';

interface ChatMessageProps {
  message: Message;
  isStreaming?: boolean;
  /** 删除某个生成文件后回调，供上层把卡片从消息里摘掉 */
  onDeleteFile?: (fileId: number) => void;
}

const FORMAT_ICONS: Record<string, string> = {
  docx: '📄',
  md: '📝',
  txt: '📃',
  pdf: '📕',
  pptx: '📽️',
};

export default function ChatMessage({ message, isStreaming, onDeleteFile }: ChatMessageProps) {
  const isUser = message.role === 'user';
  const [copied, setCopied] = useState(false);
  const [previewFile, setPreviewFile] = useState<GeneratedFile | null>(null);
  const { user } = useAuth();
  const userAvatar = user?.avatar ? `${API_HOST}${user.avatar}` : null;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = message.content;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  const handleDownloadFile = async (fileId: number, fileName: string) => {
    try {
      await downloadAiFile(fileId, fileName);
    } catch (err) {
      console.error('Download failed:', err);
    }
  };

  const handleDeleteFile = async (fileId: number) => {
    try {
      await deleteAiFile(fileId);
      // 从当前消息里摘掉这张卡片，避免删除后还显示、点了报错
      onDeleteFile?.(fileId);
    } catch (err) {
      console.error('Delete failed:', err);
      antdMessage.error('删除失败，请重试');
    }
  };

  // 只记录要预览哪个文件，取内容与格式分派都由公共 FilePreview 组件负责
  const openPreview = (file: GeneratedFile) => {
    setPreviewFile(file);
  };

  const previewSource: PreviewSource | null = previewFile
    ? {
        kind: 'url',
        url: `${API_BASE}/ai-chat/files/${previewFile.fileId}/download`,
        fileName: previewFile.fileName,
        format: previewFile.format,
      }
    : null;

  return (
    <div
      className="chat-msg-row"
      style={{
        display: 'flex',
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        marginBottom: 16,
        padding: '0 16px',
        position: 'relative',
      }}
    >
      {!isUser && (
        <AiAvatar size={32} fontSize={14} style={{ marginRight: 8 }} />
      )}
      <div style={{ maxWidth: '75%', position: 'relative' }}>
        {/* 思考/工具调用过程：显示在回复气泡上方。
            流式中的临时消息（无 id）强制展开看实时进度；
            已落库/历史消息按折叠态展示，可点击展开回看 */}
        {!isUser && message.steps && message.steps.length > 0 && (
          <AgentProgress steps={message.steps} running={!!isStreaming && !message.id} />
        )}
        <div
          style={{
            padding: '10px 14px',
            borderRadius: isUser ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
            background: isUser
              ? 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
              : '#f0f2f5',
            color: isUser ? '#fff' : '#333',
            fontSize: 14,
            lineHeight: 1.6,
            wordBreak: 'break-word',
            position: 'relative',
          }}
        >
          {isUser ? (
            <span style={{ whiteSpace: 'pre-wrap' }}>{message.content}</span>
          ) : (
            <div className="chat-markdown">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={{
                  p: ({ children }) => (
                    <p style={{ margin: '0 0 8px 0' }}>{children}</p>
                  ),
                  pre: ({ children }) => (
                    <pre
                      style={{
                        background: '#f8f9fa',
                        color: '#333',
                        padding: '12px',
                        borderRadius: '8px',
                        overflow: 'auto',
                        fontSize: 13,
                        lineHeight: 1.6,
                        margin: '8px 0',
                        border: '1px solid #e8e8e8',
                      }}
                    >
                      {children}
                    </pre>
                  ),
                  code: ({ className, children, ...props }) => {
                    const isInline = !className;
                    if (isInline) {
                      return (
                        <code
                          style={{
                            background: '#e8e8e8',
                            padding: '2px 6px',
                            borderRadius: '4px',
                            fontSize: 13,
                          }}
                          {...props}
                        >
                          {children}
                        </code>
                      );
                    }
                    return (
                      <code className={className} {...props}>
                        {children}
                      </code>
                    );
                  },
                  table: ({ children }) => (
                    <div style={{ overflow: 'auto', margin: '8px 0' }}>
                      <table
                        style={{
                          borderCollapse: 'collapse',
                          width: '100%',
                          fontSize: 13,
                        }}
                      >
                        {children}
                      </table>
                    </div>
                  ),
                  th: ({ children }) => (
                    <th
                      style={{
                        border: '1px solid #d9d9d9',
                        padding: '6px 12px',
                        background: '#fafafa',
                        textAlign: 'left',
                      }}
                    >
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td
                      style={{
                        border: '1px solid #d9d9d9',
                        padding: '6px 12px',
                      }}
                    >
                      {children}
                    </td>
                  ),
                  ul: ({ children }) => (
                    <ul style={{ margin: '4px 0', paddingLeft: 20 }}>{children}</ul>
                  ),
                  ol: ({ children }) => (
                    <ol style={{ margin: '4px 0', paddingLeft: 20 }}>{children}</ol>
                  ),
                  li: ({ children }) => (
                    <li style={{ margin: '2px 0' }}>{children}</li>
                  ),
                  h1: ({ children }) => (
                    <h1 style={{ fontSize: 18, margin: '8px 0 4px 0' }}>{children}</h1>
                  ),
                  h2: ({ children }) => (
                    <h2 style={{ fontSize: 16, margin: '8px 0 4px 0' }}>{children}</h2>
                  ),
                  h3: ({ children }) => (
                    <h3 style={{ fontSize: 15, margin: '8px 0 4px 0' }}>{children}</h3>
                  ),
                  blockquote: ({ children }) => (
                    <blockquote
                      style={{
                        borderLeft: '3px solid #667eea',
                        paddingLeft: 12,
                        margin: '8px 0',
                        color: '#666',
                      }}
                    >
                      {children}
                    </blockquote>
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
              {isStreaming && (
                <span
                  style={{
                    display: 'inline-block',
                    width: 6,
                    height: 16,
                    background: '#667eea',
                    marginLeft: 2,
                    animation: 'blink 1s infinite',
                  }}
                />
              )}
            </div>
          )}
        </div>

        {/* 复制：悬浮在气泡下方显示，不再绝对定位压在正文右下角（长回复会被盖住末行） */}
        {!isUser && !isStreaming && message.content && (
          <div
            className="chat-copy-btn"
            onClick={handleCopy}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              marginTop: 4,
              width: 'fit-content',
              cursor: 'pointer',
              fontSize: 11,
              color: '#999',
              opacity: 0,
              transition: 'opacity 0.15s',
              userSelect: 'none',
            }}
          >
            {copied ? <CheckOutlined style={{ color: '#52c41a', fontSize: 11 }} /> : <CopyOutlined style={{ fontSize: 11 }} />}
            {copied ? '已复制' : '复制'}
          </div>
        )}

        {/* User uploaded attachments */}
        {isUser && message.attachments && message.attachments.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6 }}>
            {message.attachments.map((att) => (
              <div
                key={att.id}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '5px 10px',
                  borderRadius: 8,
                  background: '#e8edff',
                  border: '1px solid #c5d0f5',
                  fontSize: 12,
                  color: '#4a5ab5',
                  maxWidth: 220,
                }}
              >
                <FileOutlined style={{ fontSize: 13, flexShrink: 0 }} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{att.name}</span>
              </div>
            ))}
          </div>
        )}

        {/* AI generated files */}
        {!isUser && message.files && message.files.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
            {message.files.map((file) => (
              <div
                key={file.fileId}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '8px 12px',
                  borderRadius: 8,
                  background: '#fff',
                  border: '1px solid #e8e8e8',
                  boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
                }}
              >
                <span style={{ fontSize: 20 }}>{FORMAT_ICONS[file.format] || '📄'}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {file.fileName}
                  </div>
                  <div style={{ fontSize: 11, color: '#999' }}>
                    {file.format.toUpperCase()} · {(file.sizeBytes / 1024).toFixed(1)}KB
                  </div>
                </div>
                <EyeOutlined
                  onClick={() => openPreview(file)}
                  style={{ fontSize: 16, color: '#667eea', cursor: 'pointer', padding: '4px' }}
                  title="预览"
                />
                <DownloadOutlined
                  onClick={() => handleDownloadFile(file.fileId, file.fileName)}
                  style={{ fontSize: 16, color: '#667eea', cursor: 'pointer', padding: '4px' }}
                  title="下载"
                />
                <Popconfirm
                  title="删除文件"
                  description="删除后可在资料库中重新生成，确定删除？"
                  onConfirm={() => handleDeleteFile(file.fileId)}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                >
                  <DeleteOutlined
                    style={{ fontSize: 16, color: '#ff4d4f', cursor: 'pointer', padding: '4px' }}
                    title="删除"
                  />
                </Popconfirm>
              </div>
            ))}
          </div>
        )}
      </div>
      {isUser && (
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: '50%',
            background: userAvatar ? 'transparent' : '#e6f7ff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginLeft: 8,
            flexShrink: 0,
            color: '#1677ff',
            fontSize: 14,
            fontWeight: 600,
            overflow: 'hidden',
          }}
        >
          {userAvatar ? (
            <img src={userAvatar} alt="我" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          ) : (
            '我'
          )}
        </div>
      )}
      <style>{`
        @keyframes blink {
          0%, 50% { opacity: 1; }
          51%, 100% { opacity: 0; }
        }
        .chat-msg-row:hover .chat-copy-btn {
          opacity: 1 !important;
        }
      `}</style>

      {/* 文件预览弹窗 - 用 Portal 挂到 body 顶层，避免被聊天窗口的 stacking context 限制 */}
      {previewFile &&
        createPortal(
          <div
            onClick={() => setPreviewFile(null)}
            style={{
              position: 'fixed',
              inset: 0,
              background: 'rgba(0,0,0,0.55)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              zIndex: 10000,
              padding: 24,
            }}
          >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: '#fff',
              borderRadius: 12,
              width: '100%',
              maxWidth: 860,
              maxHeight: '85vh',
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
              boxShadow: '0 20px 60px rgba(0,0,0,0.25)',
            }}
          >
            {/* 弹窗标题 */}
            <div
              style={{
                padding: '14px 18px',
                borderBottom: '1px solid #f0f0f0',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                <span style={{ fontSize: 18 }}>{FORMAT_ICONS[previewFile.format] || '📄'}</span>
                <span style={{ fontWeight: 600, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {previewFile.fileName}
                </span>
                <span style={{ fontSize: 12, color: '#999', flexShrink: 0 }}>
                  {previewFile.format.toUpperCase()} · {(previewFile.sizeBytes / 1024).toFixed(1)}KB
                </span>
              </div>
              <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  onClick={() => handleDownloadFile(previewFile.fileId, previewFile.fileName)}
                >
                  下载
                </Button>
                <CloseOutlined
                  onClick={() => setPreviewFile(null)}
                  style={{ fontSize: 18, color: '#999', cursor: 'pointer' }}
                />
              </div>
            </div>

            {/* 预览内容：公共组件负责取内容、判格式、分派渲染与三态 */}
            <div style={{ flex: 1, overflow: 'auto', padding: 16, background: '#fafafa' }}>
              {previewSource && <FilePreview source={previewSource} height={520} />}
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
