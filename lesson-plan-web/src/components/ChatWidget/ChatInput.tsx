'use client';

import React, { useState, useRef, useEffect } from 'react';
import { message } from 'antd';
import {
  PaperClipOutlined, CloseCircleOutlined, AudioOutlined, SendOutlined, GlobalOutlined,
} from '@ant-design/icons';
import type { MessageAttachment } from './types';
import { uploadChatFile } from '@/lib/aiChatApi';

interface PendingFile {
  file: File;
  uploading: boolean;
  uploaded?: MessageAttachment;
  error?: string;
}

interface ChatInputProps {
  /** webSearch：本轮是否允许助手联网搜索（输入区的「联网」开关） */
  onSend: (message: string, attachments?: MessageAttachment[], webSearch?: boolean) => void;
  loading: boolean;
  /** Agent 正在跑时提供：点击中断本次生成 */
  onStop?: () => void;
  /** 联网开关的状态摆在 ChatWindow：回复下方的 chips 也要按它当前状态发消息 */
  webSearch: boolean;
  onWebSearchChange: (value: boolean) => void;
}

export default function ChatInput({ onSend, loading, onStop, webSearch, onWebSearchChange }: ChatInputProps) {
  const [value, setValue] = useState('');
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [listening, setListening] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<any>(null);
  const voiceFinalRef = useRef('');
  /** 指针是否还按在话筒键上——决定识别被浏览器结束时要不要续录 */
  const holdingRef = useRef(false);
  const restartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 卸载收尾：别让识别器在组件销毁后还往回写文本
  useEffect(
    () => () => {
      holdingRef.current = false;
      if (restartTimerRef.current) clearTimeout(restartTimerRef.current);
      try {
        recognitionRef.current?.abort();
      } catch {}
    },
    [],
  );

  const startVoice = (e: React.PointerEvent) => {
    if (listening) return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      message.warning('当前浏览器不支持语音输入，请使用 Chrome 或 Edge');
      return;
    }
    // 话筒键只有 32px，按住时手指很容易滑出边界：把指针捕获到本元素，
    // 松开事件才会回到这里（旧实现靠 onPointerLeave 结束，滑出去一点就断录）
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {}
    holdingRef.current = true;

    const recognition = new SR();
    recognition.lang = 'zh-CN';
    recognition.interimResults = true;
    recognition.continuous = true;
    voiceFinalRef.current = value.trim() ? value.trim() + '，' : '';
    recognition.onresult = (ev: any) => {
      // ev.results 是累计结果，必须从 resultIndex 开始只处理本次新增的，否则会重复追加
      const startIdx = ev.resultIndex ?? 0;
      let interim = '';
      let final = '';
      for (let i = startIdx; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) final += r[0].transcript;
        else interim += r[0].transcript;
      }
      if (final) {
        voiceFinalRef.current += final;
      }
      if (interim) {
        setValue(voiceFinalRef.current + interim);
      } else {
        setValue(voiceFinalRef.current);
      }
    };
    recognition.onerror = (ev: any) => {
      // 只有拿不到麦克风才是致命错误，需要真的收；no-speech / network / aborted 交给 onend 续录
      if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
        holdingRef.current = false;
        message.warning('无法使用麦克风，请检查浏览器权限设置');
      } else if (ev.error === 'audio-capture') {
        holdingRef.current = false;
        message.warning('未检测到麦克风设备');
      }
    };
    // continuous 也拦不住浏览器在一段静音后主动结束识别——这就是"长按会暂停"的根因。
    // 交互约定是"松开才停"，所以只要还按着就重启续录；已确定的文字存在 voiceFinalRef，不会丢字。
    recognition.onend = () => {
      if (!holdingRef.current) {
        setListening(false);
        return;
      }
      restartTimerRef.current = setTimeout(() => {
        restartTimerRef.current = null;
        if (!holdingRef.current) return;
        try {
          recognition.start();
        } catch {
          holdingRef.current = false;
          setListening(false);
        }
      }, 120);
    };
    recognitionRef.current = recognition;
    setListening(true);
    try {
      recognition.start();
    } catch {
      holdingRef.current = false;
      setListening(false);
    }
  };

  const stopVoice = () => {
    if (!listening) return;
    holdingRef.current = false;
    if (restartTimerRef.current) {
      clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
    try {
      recognitionRef.current?.stop();
    } catch {}
    setListening(false);
  };

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const newFiles: PendingFile[] = Array.from(files).map((file) => ({
      file,
      uploading: true,
    }));

    setPendingFiles((prev) => [...prev, ...newFiles]);

    // 一次选多张时并行上传：串行会把等待时间叠成 5 倍，每张各自回填状态条
    await Promise.all(
      newFiles.map(async (pf) => {
        try {
          const attachment = await uploadChatFile(pf.file);
          setPendingFiles((prev) =>
            prev.map((f) => (f.file === pf.file ? { ...f, uploading: false, uploaded: attachment } : f)),
          );
        } catch (err: any) {
          setPendingFiles((prev) =>
            prev.map((f) => (f.file === pf.file ? { ...f, uploading: false, error: err.message || '上传失败' } : f)),
          );
        }
      }),
    );

    // Reset input
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const removeFile = (file: File) => {
    setPendingFiles((prev) => prev.filter((f) => f.file !== file));
  };

  const handleSend = () => {
    const text = value.trim();
    const uploadedAttachments = pendingFiles
      .filter((f) => f.uploaded)
      .map((f) => f.uploaded!);

    console.log('[ChatInput] handleSend:', { text: text.substring(0, 30), pendingFiles: pendingFiles.length, uploaded: uploadedAttachments.length });

    if (!text && uploadedAttachments.length === 0) return;
    if (loading) return;

    onSend(text || '(请查看附件)', uploadedAttachments, webSearch);
    setValue('');
    setPendingFiles([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value);
    const textarea = e.target;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
  };

  const canSend = (value.trim() || pendingFiles.some((f) => f.uploaded)) && !loading;

  return (
    <div
      style={{
        padding: '12px 16px',
        borderTop: '1px solid #f0f0f0',
        background: '#fff',
      }}
    >
      {/* Pending files */}
      {pendingFiles.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {pendingFiles.map((pf, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                padding: '4px 8px',
                borderRadius: 6,
                background: pf.error ? '#fff2f0' : pf.uploading ? '#f0f5ff' : '#f6ffed',
                border: `1px solid ${pf.error ? '#ffccc7' : pf.uploading ? '#d6e4ff' : '#b7eb8f'}`,
                fontSize: 12,
                color: pf.error ? '#ff4d4f' : '#333',
              }}
            >
              <PaperClipOutlined style={{ fontSize: 11 }} />
              <span style={{ maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {pf.file.name}
              </span>
              {pf.uploading && <span style={{ color: '#999' }}>上传中...</span>}
              {pf.error && <span style={{ color: '#ff4d4f' }}>{pf.error}</span>}
              {!pf.uploading && (
                <CloseCircleOutlined
                  onClick={() => removeFile(pf.file)}
                  style={{ fontSize: 12, color: '#999', cursor: 'pointer' }}
                />
              )}
            </div>
          ))}
        </div>
      )}

      <div
        style={{
          background: '#f7f8fa',
          border: '1px solid #eceef2',
          borderRadius: 14,
          padding: '10px 12px 8px',
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,.pdf,.doc,.docx,.txt,.md,.json,.csv,.xls,.xlsx"
          style={{ display: 'none' }}
          onChange={handleFileSelect}
        />

        {/* 正文区单独一行，控件统一收到下面一行：输入框不再被按钮挤窄 */}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          // 录音期间锁住输入：识别结果会持续覆盖整段文本，这时手打的字会被吞掉
          readOnly={listening}
          placeholder={listening ? '正在聆听… 松开结束' : '输入消息...'}
          rows={1}
          style={{
            display: 'block',
            width: '100%',
            border: 'none',
            outline: 'none',
            background: 'transparent',
            resize: 'none',
            fontSize: 14,
            lineHeight: '1.5',
            maxHeight: 120,
            padding: 0,
            fontFamily: 'inherit',
            cursor: listening ? 'default' : 'text',
          }}
        />

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
          <span
            onClick={() => fileInputRef.current?.click()}
            title="上传附件"
            style={{
              width: 28,
              height: 28,
              borderRadius: '50%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#8c8c8c',
              cursor: 'pointer',
              flexShrink: 0,
              transition: 'color 0.15s, background 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = '#667eea';
              e.currentTarget.style.background = '#eceafd';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = '#8c8c8c';
              e.currentTarget.style.background = 'transparent';
            }}
          >
            <PaperClipOutlined style={{ fontSize: 15 }} />
          </span>
          {/* 联网开关：默认关。开着时这条消息才带 webSearch，助手才会去搜教案/资料库之外的网页 */}
          <div
            onClick={() => onWebSearchChange(!webSearch)}
            title={webSearch ? '助手可以联网搜索公开网页资料' : '助手只用站内教案与资料库'}
            style={{
              height: 28,
              padding: '0 10px',
              borderRadius: 14,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              cursor: 'pointer',
              flexShrink: 0,
              userSelect: 'none',
              fontSize: 12,
              background: webSearch ? 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)' : '#fff',
              border: webSearch ? '1px solid transparent' : '1px solid #e0e2e8',
              color: webSearch ? '#fff' : '#8c8c8c',
              transition: 'background 0.2s, color 0.2s',
            }}
          >
            <GlobalOutlined style={{ fontSize: 13 }} />
            联网
          </div>

          <div style={{ flex: 1 }} />

          {/* Voice input button - press and hold to talk */}
          <div
            onPointerDown={startVoice}
            onPointerUp={stopVoice}
            onPointerCancel={stopVoice}
            title="按住说话，松开结束"
            style={{
              width: 38,
              height: 38,
              borderRadius: '50%',
              background: listening
                ? 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
                : '#fff',
              border: listening ? '1px solid transparent' : '1px solid #e0e2e8',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              userSelect: 'none',
              touchAction: 'none',
              flexShrink: 0,
              transition: 'background 0.2s',
            }}
          >
            <AudioOutlined style={{ fontSize: 18, color: listening ? '#fff' : '#8c8c8c' }} />
          </div>
          {/* 运行中这枚键变停止键：中断本次生成（已流出的正文保留） */}
          <div
            onClick={loading ? onStop : handleSend}
            title={loading ? '停止生成' : undefined}
            style={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              background: loading
                ? 'linear-gradient(135deg, #ff4d4f 0%, #ff7875 100%)'
                : canSend
                  ? 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
                  : '#d9d9d9',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: loading ? 'pointer' : canSend ? 'pointer' : 'not-allowed',
              flexShrink: 0,
              transition: 'background 0.2s',
            }}
          >
            {loading ? (
              <span style={{ width: 10, height: 10, borderRadius: 2, background: '#fff', display: 'block' }} />
            ) : (
              <SendOutlined style={{ fontSize: 14, color: '#fff' }} />
            )}
          </div>
        </div>
      </div>
      <div
        style={{
          textAlign: 'center',
          fontSize: 11,
          color: '#bbb',
          marginTop: 6,
        }}
      >
        {listening ? (
          <span style={{ color: '#667eea' }}>正在聆听… 松开结束</span>
        ) : (
          '按住话筒说话，松开结束 · Enter 发送'
        )}
      </div>
    </div>
  );
}
