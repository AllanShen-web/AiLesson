'use client';

import React, { useEffect, useRef, useState, useCallback, useSyncExternalStore } from 'react';
import { Spin, Button, Input, Popconfirm } from 'antd';
import {
  MenuOutlined,
  CloseOutlined,
  ExpandAltOutlined,
  FullscreenOutlined,
  BlockOutlined,
  PlusOutlined,
  DeleteOutlined,
  MessageOutlined,
  FilePptOutlined,
  CheckOutlined,
  RedoOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons';
import ChatMessage from './ChatMessage';
import ChatInput from './ChatInput';
import AiAvatar from './AiAvatar';
import AgentProgress, { type ProgressStep } from './AgentProgress';
import type { Message, Conversation, MessageAttachment } from './types';
import { useMobile } from '@/hooks/useMobile';
import { chatUiStore, type ChatMode } from '@/lib/chatUiStore';
import type { ConfirmationPayload, ChoicePayload } from '@/lib/aiChatApi';

interface ChatWindowProps {
  visible: boolean;
  messages: Message[];
  loading: boolean;
  streamingContent: string;
  conversations: Conversation[];
  currentConversationId: number | null;
  agentMode?: boolean;
  /** 在跑任务的实时状态是否属于当前查看的会话（切到别的对话时任务照跑，但这些界面要收起来） */
  runLive?: boolean;
  selectedFieldLabel?: string | null;
  onSend: (message: string, attachments?: MessageAttachment[], webSearch?: boolean) => void;
  onClose: () => void;
  onSelectConversation: (id: number) => void;
  onNewConversation: () => void;
  onDeleteConversation: (id: number) => void;
  onDeselectField?: () => void;
  /** 回复下方的引导建议：模型给出的下一步（后端已按候选白名单校验），点击即作为消息发出 */
  suggestions?: string[];
  onSuggestion?: (text: string, webSearch?: boolean) => void;
  /** 停止生成：中断本次 SSE 请求 */
  onStop?: () => void;
  /** 删除某个生成文件后回调，供上层把卡片从消息里摘掉 */
  onDeleteFile?: (fileId: number) => void;
  /** Agent 的处理步骤（思考/工具调用），折叠展示，不走消息流 */
  agentSteps?: ProgressStep[];
  /** 通用工具确认卡片：关键工具执行前挂起 */
  confirmation?: ConfirmationPayload | null;
  /** 用户操作：批准 / 拒绝（可带反馈）/ 取消 */
  onConfirmation?: (decision: 'approved' | 'rejected' | 'cancel', feedback?: string) => void;
  /** askChoice 选项卡片：需要用户做选择时挂在输入框上方 */
  choice?: ChoicePayload | null;
  /** 点完最后一问：各问选中的文字按问的顺序用换行拼成一条消息发给 Agent */
  onChoiceSelect?: (message: string) => void;
}

export default function ChatWindow({
  visible,
  messages,
  loading,
  streamingContent,
  conversations,
  currentConversationId,
  agentMode,
  runLive = true,
  selectedFieldLabel,
  onSend,
  onClose,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  onDeselectField,
  suggestions,
  onSuggestion,
  onStop,
  onDeleteFile,
  agentSteps,
  confirmation,
  onConfirmation,
  choice,
  onChoiceSelect,
}: ChatWindowProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const windowRef = useRef<HTMLDivElement>(null);
  const isMobile = useMobile();
  const [isAtBottom, setIsAtBottom] = useState(true);
  // 联网开关摆在窗口层而非输入区：回复下方的 chips 也要按它当前状态发消息
  const [webSearch, setWebSearch] = useState(false);
  const prevLoadingRef = useRef(false);
  // 通用卡片「拒绝并调整」输入态
  const [showReject, setShowReject] = useState(false);
  const [rejectText, setRejectText] = useState('');
  /** 选项卡片的答题进度：一卡多问时逐问显示，answers[i] 记第 i 问选中的文字 */
  const [qStep, setQStep] = useState(0);
  const [answers, setAnswers] = useState<string[]>([]);

  useEffect(() => {
    prevLoadingRef.current = loading;
  }, [loading]);

  // Window mode 提升到 chatUiStore：AppLayout 也要知道是否停靠，才能挤压正文实现分栏
  const mode = useSyncExternalStore(chatUiStore.subscribe, () => chatUiStore.mode);
  const setMode = (m: ChatMode) => {
    chatUiStore.setMode(m);
    setDragPos(null);
  };

  // Drag for window
  const [dragPos, setDragPos] = useState<{ top: number; left: number } | null>(null);
  const dragRef = useRef<{
    startX: number;
    startY: number;
    startTop: number;
    startLeft: number;
    moved: boolean;
  } | null>(null);

  const handleDragStart = useCallback(
    (e: React.MouseEvent) => {
      if (mode !== 'float') return;
      const rect = windowRef.current?.getBoundingClientRect();
      if (!rect) return;
      dragRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        startTop: dragPos?.top ?? rect.top,
        startLeft: dragPos?.left ?? rect.left,
        moved: false,
      };
      const handleMove = (ev: MouseEvent) => {
        if (!dragRef.current) return;
        const dx = ev.clientX - dragRef.current.startX;
        const dy = ev.clientY - dragRef.current.startY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
          dragRef.current.moved = true;
        }
        setDragPos({
          top: Math.max(0, dragRef.current.startTop + dy),
          left: Math.max(0, Math.min(window.innerWidth - 100, dragRef.current.startLeft + dx)),
        });
      };
      const handleUp = () => {
        document.removeEventListener('mousemove', handleMove);
        document.removeEventListener('mouseup', handleUp);
      };
      document.addEventListener('mousemove', handleMove);
      document.addEventListener('mouseup', handleUp);
    },
    [mode, dragPos],
  );

  // 嵌入·窄栏：拖动左边缘改变面板宽度（悬浮与满栏不适用）
  const [dockWidth, setDockWidth] = useState(376);
  const dockResizeRef = useRef<{ startX: number; startW: number } | null>(null);

  const handleDockResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dockResizeRef.current = { startX: e.clientX, startW: dockWidth };
      const handleMove = (ev: MouseEvent) => {
        if (!dockResizeRef.current) return;
        const delta = dockResizeRef.current.startX - ev.clientX; // 向左拖 = 变宽
        const w = Math.min(
          Math.max(320, dockResizeRef.current.startW + delta),
          Math.floor(window.innerWidth * 0.6),
        );
        setDockWidth(w);
      };
      const handleUp = () => {
        dockResizeRef.current = null;
        document.removeEventListener('mousemove', handleMove);
        document.removeEventListener('mouseup', handleUp);
      };
      document.addEventListener('mousemove', handleMove);
      document.addEventListener('mouseup', handleUp);
    },
    [dockWidth],
  );

  // 悬浮小窗：拖动左边缘/下边缘/左下角改变宽高（宽度以右边缘为基准，所以拖左边）
  const [floatSize, setFloatSize] = useState({ width: 480, height: 580 });
  const floatResizeRef = useRef(false);

  const handleFloatResizeStart = useCallback(
    (axis: 'x' | 'y' | 'both') => (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const rect = windowRef.current?.getBoundingClientRect();
      if (!rect) return;
      setDragPos({ top: rect.top, left: rect.left });
      floatResizeRef.current = true;
      const startX = e.clientX;
      const startY = e.clientY;
      const startW = rect.width;
      const startH = rect.height;
      const startLeft = rect.left;
      // 左边缘最多拖到离屏幕左边 8px
      const maxW = Math.max(320, Math.floor(startW + rect.left - 8));
      const maxH = Math.max(380, Math.floor(window.innerHeight - rect.top - 8));
      const handleMove = (ev: MouseEvent) => {
        let nextW = startW;
        if (axis !== 'y') {
          nextW = Math.min(Math.max(320, startW + (startX - ev.clientX)), maxW);
          // 右边缘钉住：变宽多少，left 就往左走多少
          setDragPos((prev) => (prev ? { ...prev, left: startLeft + startW - nextW } : prev));
        }
        setFloatSize((prev) => ({
          width: nextW,
          height: axis === 'x' ? prev.height : Math.min(Math.max(380, startH + (ev.clientY - startY)), maxH),
        }));
      };
      const handleUp = () => {
        floatResizeRef.current = false;
        document.removeEventListener('mousemove', handleMove);
        document.removeEventListener('mouseup', handleUp);
      };
      document.addEventListener('mousemove', handleMove);
      document.addEventListener('mouseup', handleUp);
    },
    [],
  );

  // Internal drawer
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    if (isAtBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, streamingContent, isAtBottom, suggestions]);

  // 确认卡片出现时强制滚到底：这是需要用户决策的提示，不能被翻过往上藏；
  // 新卡片出现时重置「拒绝并调整」输入态
  useEffect(() => {
    if (confirmation) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
      setIsAtBottom(true);
      setShowReject(false);
      setRejectText('');
    }
  }, [confirmation]);

  // 选项卡片同样是需要用户决策的提示，出现时强制滚到底；新卡到来时答题进度归零
  useEffect(() => {
    if (choice) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
      setIsAtBottom(true);
      setQStep(0);
      setAnswers([]);
    }
  }, [choice]);

  /** 点选一问的选项：记下答案并跳到下一问；最后一问点完，把各问所选按顺序拼成一条消息发出 */
  const pickChoice = (label: string) => {
    const total = choice?.questions.length ?? 0;
    const next = [...answers];
    next[qStep] = label;
    if (qStep + 1 >= total) {
      onChoiceSelect?.(next.slice(0, total).join('\n'));
      return;
    }
    setAnswers(next);
    setQStep(qStep + 1);
  };

  const handleScroll = () => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    setIsAtBottom(atBottom);
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    setIsAtBottom(true);
  };

  /**
   * 回复下方的引导 chips：点一条就把这句话发给助手。
   * 措辞与后端候选白名单同源，所以点开的指令一定有对应工具能执行。
   * centered=true 用于空会话（跟在欢迎语后面居中），false 用于最新回复下方（与 AI 气泡左对齐）。
   */
  const renderChips = (centered: boolean) => {
    if (!suggestions || suggestions.length === 0) return null;
    return (
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 8,
          padding: centered ? '16px 12px 0' : '2px 16px 4px 56px',
          justifyContent: centered ? 'center' : 'flex-start',
        }}
      >
        {suggestions.map((text) => (
          <div
            key={text}
            className="chat-suggest-chip"
            onClick={() => onSuggestion?.(text, webSearch)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              padding: '6px 12px',
              borderRadius: 999,
              background: '#f5f6fa',
              border: '1px solid #e6e8f0',
              color: '#4a5568',
              fontSize: 13,
              lineHeight: 1.4,
              cursor: 'pointer',
              userSelect: 'none',
              maxWidth: '100%',
            }}
          >
            <span>{text}</span>
            <span style={{ color: '#667eea', flexShrink: 0 }}>→</span>
          </div>
        ))}
      </div>
    );
  };

  if (!visible) return null;

  // Compute window styles based on mode
  const getWindowStyle = (): React.CSSProperties => {
    const base: React.CSSProperties = {
      display: 'flex',
      flexDirection: 'column',
      background: '#fff',
      overflow: 'hidden',
    };

    if (isMobile) {
      return { ...base, position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh', borderRadius: 0, zIndex: 9999 };
    }

    // 嵌入·窄栏：flex 子项靠右停靠，正文自动让位（挤压分栏），不遮挡内容
    // 高度交给 flex 的 align-items:stretch，再减掉上下 margin；写 100% 会溢出容器 16px
    if (mode === 'dock') {
      return {
        ...base,
        position: 'relative',
        flex: `0 0 ${dockWidth}px`,
        alignSelf: 'stretch',
        minHeight: 0,
        margin: '8px 8px 8px 0',
        borderRadius: 8,
        border: '1px solid #f0f0f0',
        boxShadow: '0 8px 40px rgba(0, 0, 0, 0.08)',
        transition: dockResizeRef.current ? 'none' : 'flex-basis 0.2s',
      };
    }

    // 嵌入·满栏：占满除侧栏外的整个内容区（正文由 AppLayout 隐藏）
    if (mode === 'wide') {
      return { ...base, position: 'relative', flex: 1, minWidth: 0, height: '100%' };
    }

    // 悬浮小窗：fixed 浮层，保留拖拽，边/角可改尺寸
    const float: React.CSSProperties = {
      ...base,
      position: 'fixed',
      width: floatSize.width,
      height: floatSize.height,
      borderRadius: 16,
      boxShadow: '0 8px 40px rgba(0, 0, 0, 0.12)',
      zIndex: 9999,
      transition: dragPos || floatResizeRef.current
        ? 'none'
        : 'width 0.2s, height 0.2s, top 0.2s, right 0.2s, bottom 0.2s, left 0.2s, border-radius 0.2s',
    };
    if (dragPos) {
      return { ...float, ...dragPos };
    }
    return { ...float, bottom: 90, right: 24 };
  };

  return (
    <div ref={windowRef} style={getWindowStyle()}>
      {/* 窄栏左边缘拖拽把手：横向调整面板宽度 */}
      {mode === 'dock' && !isMobile && (
        <div
          onMouseDown={handleDockResizeStart}
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            bottom: 0,
            width: 8,
            cursor: 'col-resize',
            zIndex: 20,
          }}
        />
      )}
      {/* 悬浮小窗边/角拖拽把手：左边缘调宽、下边缘调高、左下角同时调。
          聊天记录抽屉从左滑出，打开时让位给它，否则左边缘 8px 点不到列表 */}
      {mode === 'float' && !isMobile && !drawerOpen && (
        <>
          <div
            onMouseDown={handleFloatResizeStart('x')}
            style={{ position: 'absolute', top: 0, left: 0, bottom: 16, width: 8, cursor: 'ew-resize', zIndex: 20 }}
          />
          <div
            onMouseDown={handleFloatResizeStart('y')}
            style={{ position: 'absolute', left: 16, right: 0, bottom: 0, height: 8, cursor: 'ns-resize', zIndex: 20 }}
          />
          <div
            onMouseDown={handleFloatResizeStart('both')}
            style={{ position: 'absolute', left: 0, bottom: 0, width: 18, height: 18, cursor: 'nesw-resize', zIndex: 21 }}
          />
        </>
      )}
      {/* Internal drawer */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          bottom: 0,
          width: 280,
          zIndex: 10,
          background: '#fff',
          borderRight: '1px solid #f0f0f0',
          transform: drawerOpen ? 'translateX(0)' : 'translateX(-100%)',
          transition: 'transform 0.25s ease',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: drawerOpen ? '2px 0 8px rgba(0,0,0,0.08)' : 'none',
        }}
      >
        <div
          style={{
            height: 56,
            padding: '0 16px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #f0f0f0',
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 15, fontWeight: 600 }}>聊天记录</span>
          <div
            onClick={() => {
              onNewConversation();
              setDrawerOpen(false);
            }}
            style={{
              padding: '4px 10px',
              borderRadius: 6,
              background: '#1677ff',
              color: '#fff',
              fontSize: 12,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            <PlusOutlined /> 新建
          </div>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {conversations.map((item) => (
            <div
              key={item.id}
              onClick={() => {
                onSelectConversation(item.id);
                setDrawerOpen(false);
              }}
              style={{
                padding: '12px 16px',
                cursor: 'pointer',
                background: currentConversationId === item.id ? '#e6f7ff' : 'transparent',
                borderLeft: currentConversationId === item.id ? '3px solid #1677ff' : '3px solid transparent',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                <MessageOutlined style={{ color: '#667eea', fontSize: 14, flexShrink: 0 }} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 14, color: '#333', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {item.title}
                  </div>
                  <div style={{ fontSize: 11, color: '#bbb', marginTop: 2 }}>
                    {new Date(item.updatedAt).toLocaleString('zh-CN')}
                  </div>
                </div>
              </div>
              {/* 包一层 span 拦冒泡：否则点删除会连带触发整行的"切换到该对话" */}
              <span onClick={(e) => e.stopPropagation()}>
                <Popconfirm
                  title="删除对话"
                  description="删除后该对话的全部聊天记录将不可恢复，确定删除？"
                  onConfirm={() => onDeleteConversation(item.id)}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                >
                  <DeleteOutlined
                    style={{ color: '#bbb', fontSize: 13, flexShrink: 0, padding: '4px' }}
                    title="删除"
                  />
                </Popconfirm>
              </span>
            </div>
          ))}
          {conversations.length === 0 && (
            <div style={{ textAlign: 'center', color: '#bbb', padding: '40px 0', fontSize: 13 }}>暂无对话</div>
          )}
        </div>
      </div>

      {/* Overlay when drawer open */}
      {drawerOpen && (
        <div
          onClick={() => setDrawerOpen(false)}
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            zIndex: 9,
            background: 'transparent',
          }}
        />
      )}

      {/* Header：只有左侧标题区是拖拽区，右侧按钮区既不触发拖拽也不显示 move 光标 */}
      <div
        style={{
          height: 56,
          padding: '0 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid #f0f0f0',
          background: '#fff',
          flexShrink: 0,
          cursor: 'default',
          zIndex: 1,
          position: 'relative',
        }}
      >
        <div
          onMouseDown={handleDragStart}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            flex: 1,
            minWidth: 0,
            height: '100%',
            cursor: mode === 'float' ? 'move' : 'default',
          }}
        >
          <MenuOutlined
            onClick={() => setDrawerOpen(!drawerOpen)}
            style={{ fontSize: 18, cursor: 'pointer', color: '#333' }}
          />
          <span style={{ fontSize: 16, fontWeight: 600, color: '#333' }}>Ailan 智慧助手</span>
        </div>
        {/* 右侧控制区：屏蔽 mousedown，避免触发头部拖拽 */}
        <div
          style={{ display: 'flex', alignItems: 'center', gap: 12, cursor: 'default', flexShrink: 0 }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          {/* 三个独立档位按钮：直接切档，当前档位主色高亮 */}
          {([
            { key: 'wide', title: '嵌入满栏', icon: <FullscreenOutlined /> },
            { key: 'dock', title: '嵌入窄栏', icon: <ExpandAltOutlined /> },
            { key: 'float', title: '悬浮小窗', icon: <BlockOutlined /> },
          ] as const).map((btn) => {
            const active = mode === btn.key;
            return (
              <span
                key={btn.key}
                title={btn.title}
                onClick={() => setMode(btn.key)}
                style={{
                  fontSize: 16,
                  cursor: 'pointer',
                  color: active ? '#1677ff' : '#999',
                  display: 'inline-flex',
                  alignItems: 'center',
                }}
              >
                {btn.icon}
              </span>
            );
          })}
          <CloseOutlined
            onClick={onClose}
            style={{ fontSize: 16, cursor: 'pointer', color: '#999' }}
          />
        </div>
      </div>

      {/* Agent bar */}
      {agentMode && (
        <div
          style={{
            padding: '6px 16px',
            background: selectedFieldLabel ? 'rgba(102, 126, 234, 0.06)' : 'rgba(255, 107, 107, 0.04)',
            borderBottom: '1px solid #f0f0f0',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontSize: 12,
            color: '#666',
            flexShrink: 0,
            zIndex: 1,
          }}
        >
          <span>
            {selectedFieldLabel ? (
              <>编辑中：<span style={{ color: '#667eea', fontWeight: 500 }}>{selectedFieldLabel}</span></>
            ) : (
              <span style={{ color: '#ee5a24' }}>Agent 模式 · 对话生成整个教案</span>
            )}
          </span>
          {selectedFieldLabel && onDeselectField && (
            <span
              onClick={onDeselectField}
              style={{ cursor: 'pointer', color: '#999', fontSize: 11 }}
            >
              取消选中
            </span>
          )}
        </div>
      )}

      {/* Messages area */}
      <div
        ref={messagesContainerRef}
        onScroll={handleScroll}
        style={{ flex: 1, overflowY: 'auto', padding: '16px 0', background: '#fff', position: 'relative', zIndex: 1 }}
      >
        {messages.length === 0 && !(loading && runLive) && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: '#bbb',
            }}
          >
            <AiAvatar size={64} fontSize={28} style={{ marginBottom: 16 }} />
            <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 4 }}>你好，我是布布</div>
            <div style={{ fontSize: 13 }}>有什么可以帮你的？</div>
            {renderChips(true)}
          </div>
        )}

        {messages.map((msg, i) => (
          <ChatMessage key={msg.id || i} message={msg} onDeleteFile={onDeleteFile} />
        ))}

        {/* Agent 处理过程（工具执行期）：此时正文还没开始流出，
            用游离面板展示进度；一旦有正文，就交给下方带 steps 的消息整体渲染，
            避免同一个面板出现两次 */}
        {loading && runLive && !streamingContent && agentSteps && agentSteps.length > 0 && (
          <div style={{ padding: '0 16px', marginBottom: 8 }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <AiAvatar size={32} fontSize={14} style={{ marginTop: 2 }} />
              <AgentProgress steps={agentSteps} running />
            </div>
          </div>
        )}

        {/* AI 正文：只有这里用打字光标。
            steps 一并传给这条临时消息，让思考面板与正文气泡作为整体渲染，
            onDone 时只是 isStreaming 翻转，面板不会发生位置跳动 */}
        {loading && runLive && streamingContent && (
          <ChatMessage
            message={{ role: 'assistant', content: streamingContent, steps: agentSteps }}
            isStreaming
          />
        )}

        {/* 模型还没开始思考（等待首包） */}
        {loading && runLive && !streamingContent && (!agentSteps || agentSteps.length === 0) && (
          <div style={{ padding: '0 16px', marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <AiAvatar size={32} fontSize={14} />
              <Spin size="small" />
              <span style={{ color: '#999', fontSize: 13 }}>思考中...</span>
            </div>
          </div>
        )}

        {/* 最新回复下方的引导下一步（空会话时 chips 跟在欢迎语里，不重复渲染） */}
        {!loading && runLive && messages.length > 0 && renderChips(false)}

        <div ref={messagesEndRef} />
      </div>

      {/* Scroll to bottom button */}
      {!isAtBottom && (
        <div
          onClick={scrollToBottom}
          style={{
            position: 'absolute',
            bottom: 80,
            left: '50%',
            transform: 'translateX(-50%)',
            width: 32,
            height: 32,
            borderRadius: '50%',
            background: '#fff',
            boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            zIndex: 5,
            transition: 'opacity 0.2s',
          }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M7 2v8M3 7l4 4 4-4" stroke="#666" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      )}

      {/* askChoice 选项卡片：一卡可含多问，逐问显示（右上翻页看进度），点完最后一问即把选择发给 Agent */}
      {choice && runLive && choice.questions.length > 0 && (() => {
        const qs = choice.questions;
        const cur = Math.min(qStep, qs.length - 1);
        const q = qs[cur];
        const canNext = answers[cur] !== undefined && cur < qs.length - 1;
        return (
          <div
            style={{
              margin: '0 16px 8px',
              border: '1px solid #e6e8f0',
              background: '#fff',
              borderRadius: 12,
              boxShadow: '0 2px 12px rgba(0, 0, 0, 0.06)',
              flexShrink: 0,
              position: 'relative',
              zIndex: 1,
              maxHeight: 260,
              overflowY: 'auto',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '11px 14px 7px' }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: '#1f2937' }}>{q.question}</span>
              {qs.length > 1 && (
                <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', flexShrink: 0 }}>
                  <span
                    onClick={cur > 0 ? () => setQStep(cur - 1) : undefined}
                    style={{
                      width: 20,
                      height: 20,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 15,
                      color: cur > 0 ? '#6b7280' : '#d1d5db',
                      cursor: cur > 0 ? 'pointer' : 'default',
                      userSelect: 'none',
                    }}
                  >
                    ‹
                  </span>
                  <span style={{ fontSize: 12, color: '#9ca3af', minWidth: 28, textAlign: 'center' }}>
                    {cur + 1}/{qs.length}
                  </span>
                  <span
                    onClick={canNext ? () => setQStep(cur + 1) : undefined}
                    style={{
                      width: 20,
                      height: 20,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 15,
                      color: canNext ? '#6b7280' : '#d1d5db',
                      cursor: canNext ? 'pointer' : 'default',
                      userSelect: 'none',
                    }}
                  >
                    ›
                  </span>
                </span>
              )}
            </div>
            {q.options.map((opt, i) => {
              const chosen = answers[cur] === opt.label;
              return (
                <div
                  key={`${cur}-${i}-${opt.label}`}
                  className="chat-choice-option"
                  onClick={() => pickChoice(opt.label)}
                  style={{
                    display: 'flex',
                    gap: 10,
                    alignItems: 'center',
                    padding: '9px 14px',
                    cursor: 'pointer',
                    borderTop: '1px solid #f2f3f7',
                    userSelect: 'none',
                    background: chosen ? '#f4f7ff' : undefined,
                  }}
                >
                  <span
                    style={{
                      width: 18,
                      height: 18,
                      flexShrink: 0,
                      borderRadius: '50%',
                      border: `1px solid ${chosen ? '#4a6cf7' : '#d9dce6'}`,
                      color: chosen ? '#4a6cf7' : '#6b7280',
                      fontSize: 12,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    {i + 1}
                  </span>
                  <span style={{ fontSize: 13, fontWeight: 500, color: '#1f2937', whiteSpace: 'nowrap' }}>
                    {opt.label}
                  </span>
                  {opt.description && (
                    <span
                      title={opt.description}
                      style={{
                        flex: 1,
                        minWidth: 0,
                        fontSize: 12,
                        color: '#6b7280',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {opt.description}
                    </span>
                  )}
                  <span className="chat-choice-arrow" style={{ fontSize: 12, color: '#9ca3af', flexShrink: 0 }}>
                    →
                  </span>
                </div>
              );
            })}
          </div>
        );
      })()}

      {/* 通用工具确认卡片：关键操作执行前由用户决定（Human-in-the-Loop） */}
      {confirmation && runLive && (
        <div
          style={{
            margin: '0 16px 8px',
            border: '1px solid #d6e4ff',
            background: '#f6f9ff',
            borderRadius: 10,
            padding: 12,
            flexShrink: 0,
            position: 'relative',
            zIndex: 1,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            {confirmation.toolName === 'create_ppt_outline' ? (
              <FilePptOutlined style={{ color: '#fa8c16', fontSize: 16 }} />
            ) : (
              <QuestionCircleOutlined style={{ color: '#fa8c16', fontSize: 16 }} />
            )}
            <span style={{ fontSize: 14, fontWeight: 600, color: '#1f2937' }}>{confirmation.title}</span>
          </div>
          <div style={{ fontSize: 13, color: '#374151', marginBottom: 8 }}>{confirmation.summary}</div>

          {confirmation.toolName === 'create_ppt_outline' ? (
            <>
              {/* 大纲摘要：最多展示 6 页，超出折叠计数 */}
              <div style={{
                maxHeight: 96, overflowY: 'auto', marginBottom: 10,
                borderTop: '1px dashed #d6e4ff', paddingTop: 8,
              }}>
                {(confirmation.data?.outline || []).map((sec: any, i: number) => (
                  <div key={i} style={{ fontSize: 12, color: '#4b5563', lineHeight: 1.7, display: 'flex', gap: 6 }}>
                    <span style={{ color: '#667eea', fontWeight: 600, flexShrink: 0 }}>{i + 1}.</span>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {sec.title}
                      {sec.points?.[0] ? ` — ${sec.points[0]}` : ''}
                    </span>
                  </div>
                ))}
                {(confirmation.data?.outline?.length || 0) > 6 && (
                  <div style={{ fontSize: 12, color: '#999' }}>…等共 {confirmation.data.outline.length} 页</div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <Button type="primary" size="small" icon={<CheckOutlined />} onClick={() => onConfirmation?.('approved')}>
                  确认生成
                </Button>
                <Button size="small" icon={<RedoOutlined />} onClick={() => onConfirmation?.('rejected')}>
                  重新生成大纲
                </Button>
                <Button size="small" type="text" onClick={() => onConfirmation?.('cancel')}>
                  取消
                </Button>
              </div>
            </>
          ) : showReject ? (
            <div>
              <Input.TextArea
                rows={2}
                value={rejectText}
                onChange={(e) => setRejectText(e.target.value)}
                placeholder="说明需要调整的地方（可选）"
              />
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <Button size="small" type="primary" onClick={() => onConfirmation?.('rejected', rejectText.trim() || undefined)}>
                  提交意见
                </Button>
                <Button size="small" type="text" onClick={() => setShowReject(false)}>
                  返回
                </Button>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 8 }}>
              <Button type="primary" size="small" icon={<CheckOutlined />} onClick={() => onConfirmation?.('approved')}>
                确认执行
              </Button>
              <Button size="small" onClick={() => setShowReject(true)}>
                拒绝并调整
              </Button>
              <Button size="small" type="text" onClick={() => onConfirmation?.('cancel')}>
                取消
              </Button>
            </div>
          )}
        </div>
      )}

      {/* Input */}
      <div style={{ position: 'relative', zIndex: 1 }}>
        <ChatInput
          onSend={onSend}
          loading={loading}
          onStop={onStop}
          webSearch={webSearch}
          onWebSearchChange={setWebSearch}
        />
      </div>
      <style>{`
        @keyframes blink { 0%, 50% { opacity: 1; } 51%, 100% { opacity: 0; } }
        .chat-suggest-chip:hover {
          background: #eef1ff !important;
          border-color: #c3ccf5 !important;
          color: #4b5cc4 !important;
        }
        .chat-choice-option:hover {
          background: #f6f7fb;
        }
        .chat-choice-arrow {
          opacity: 0;
        }
        .chat-choice-option:hover .chat-choice-arrow {
          opacity: 1;
        }
      `}</style>
    </div>
  );
}
