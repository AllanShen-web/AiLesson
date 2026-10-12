'use client';

import { useState, useCallback, useRef } from 'react';
import { streamChat, getConversations, getMessages, deleteConversation } from '@/lib/aiChatApi';
import type { Message, Conversation, MessageAttachment } from './types';

interface SendMessageOptions {
  onToken?: (token: string, accumulated: string) => void;
  onDone?: (fullContent: string) => void;
  skipAssistantMessage?: boolean;
}

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentConversationId, setCurrentConversationId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [streamingContent, setStreamingContent] = useState('');
  const abortRef = useRef(false);
  const contentRef = useRef('');
  const callbacksRef = useRef<SendMessageOptions>({});

  const loadConversations = useCallback(async () => {
    try {
      const data = await getConversations();
      setConversations(Array.isArray(data) ? data : []);
    } catch {
      setConversations([]);
    }
  }, []);

  const loadMessages = useCallback(async (conversationId: number) => {
    try {
      const data = await getMessages(conversationId);
      const msgs = (Array.isArray(data) ? data : []).map((m: any) => ({
        ...m,
        files: m.generatedFiles || m.files || undefined,
        // 历史消息也带回思考步骤，折叠面板才能展开回看
        steps: Array.isArray(m.steps) && m.steps.length > 0 ? m.steps : undefined,
      }));
      console.log('[useChat] loaded messages:', msgs.length, 'first msg attachments:', msgs[0]?.attachments);
      setMessages(msgs);
      setCurrentConversationId(conversationId);
    } catch {
      setMessages([]);
    }
  }, []);

  const newConversation = useCallback(() => {
    setMessages([]);
    setCurrentConversationId(null);
    setStreamingContent('');
    contentRef.current = '';
  }, []);

  const removeConversation = useCallback(async (id: number) => {
    await deleteConversation(id);
    if (currentConversationId === id) {
      newConversation();
    }
    await getConversations().then((data) =>
      setConversations(Array.isArray(data) ? data : []),
    );
  }, [currentConversationId, newConversation]);

  const sendMessage = useCallback(
    async (content: string, attachments?: MessageAttachment[]) => {
      if (loading || abortRef.current) return;

      callbacksRef.current = {};

      const userMessage: Message = { role: 'user', content, attachments };
      setMessages((prev) => [...prev, userMessage]);
      setLoading(true);
      setStreamingContent('');
      contentRef.current = '';
      abortRef.current = false;

      await streamChat(
        content,
        currentConversationId,
        (token) => {
          contentRef.current += token;
          setStreamingContent(contentRef.current);
          callbacksRef.current.onToken?.(token, contentRef.current);
        },
        async (messageId, conversationId) => {
          const finalContent = contentRef.current;
          setMessages((prev) => [
            ...prev,
            { id: messageId, role: 'assistant', content: finalContent },
          ]);
          setStreamingContent('');
          contentRef.current = '';
          setLoading(false);
          setCurrentConversationId(conversationId);
          abortRef.current = false;
          callbacksRef.current.onDone?.(finalContent);
          callbacksRef.current = {};
          await getConversations().then((data) =>
            setConversations(Array.isArray(data) ? data : []),
          );
        },
        (error) => {
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: `抱歉，发生了错误: ${error}` },
          ]);
          setStreamingContent('');
          contentRef.current = '';
          setLoading(false);
          abortRef.current = false;
          callbacksRef.current = {};
        },
      );
    },
    [loading, currentConversationId],
  );

  const addMessage = useCallback((msg: Message) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  /** 从所有消息里移除某个生成文件（删除后同步摘掉聊天卡片） */
  const removeGeneratedFile = useCallback((fileId: number) => {
    setMessages((prev) =>
      prev.map((msg) =>
        msg.files?.some((f) => f.fileId === fileId)
          ? { ...msg, files: msg.files.filter((f) => f.fileId !== fileId) }
          : msg,
      ),
    );
  }, []);

  const updateLastMessage = useCallback((updater: (msg: Message) => Message) => {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (!last) return prev;
      return [...prev.slice(0, -1), updater(last)];
    });
  }, []);

  const removeLastMessage = useCallback(() => {
    setMessages((prev) => prev.slice(0, -1));
  }, []);

  return {
    messages,
    conversations,
    currentConversationId,
    loading,
    streamingContent,
    loadConversations,
    loadMessages,
    newConversation,
    removeConversation,
    sendMessage,
    addMessage,
    updateLastMessage,
    removeLastMessage,
    removeGeneratedFile,
    setCurrentConversationId,
    setStreamingContent,
  };
}
