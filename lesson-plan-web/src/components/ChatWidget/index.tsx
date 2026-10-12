'use client';

import { useState, useEffect, useCallback, useSyncExternalStore, useRef } from 'react';
import { useRouter } from 'next/navigation';
import ChatBall from './ChatBall';
import ChatWindow from './ChatWindow';
import { useChat } from './useChat';
import { formAgentStore } from '@/lib/formAgentStore';
import { chatUiStore } from '@/lib/chatUiStore';
import { pptPreviewStore } from '@/lib/pptPreviewStore';
import { pptAgentStore } from '@/lib/pptAgentStore';
import { agentChat, type ConfirmationPayload, type ChoicePayload } from '@/lib/aiChatApi';
import type { MessageAttachment, GeneratedFile } from './types';
import type { ProgressStep } from './AgentProgress';

const BASIC_FIELD_LABELS: Record<string, string> = {
  'basic-topic': '课题',
  'basic-unit': '单元',
  'basic-subject': '学科',
  'basic-grade': '年级',
};

export default function ChatWidget() {
  const router = useRouter();
  const visible = useSyncExternalStore(chatUiStore.subscribe, () => chatUiStore.visible);
  const pending = useSyncExternalStore(chatUiStore.subscribe, () => chatUiStore.pending);
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentSteps, setAgentSteps] = useState<ProgressStep[]>([]);
  /** 模型给出的下一步引导（后端按候选白名单校验过），渲染在最新回复下方 */
  const [suggestions, setSuggestions] = useState<string[]>([]);
  /** 通用工具确认卡片：关键工具执行前挂起，等用户批准/拒绝 */
  const [confirmationCard, setConfirmationCard] = useState<ConfirmationPayload | null>(null);
  /** askChoice 选项卡片：用户在点选前一直挂在输入框上方 */
  const [choiceCard, setChoiceCard] = useState<ChoicePayload | null>(null);
  const streamingRef = useRef('');
  /** 本次请求的取消句柄：停止生成时 abort，后端据连接关闭中止在途模型/工具调用 */
  const agentAbortRef = useRef<AbortController | null>(null);
  const abortedRef = useRef(false);
  /** 在跑任务自己的一份缓存：切到别的对话只是不展示，任务照跑、进度都记在这里（null=空闲） */
  const runRef = useRef<{
    convId: number | null;
    text: string;
    steps: ProgressStep[];
    suggestions: string[];
    confirmation: ConfirmationPayload | null;
    choice: ChoicePayload | null;
  } | null>(null);
  // 视图当前停在哪条会话（随渲染刷新）：只有正对着任务所属会话时才把缓存同步进界面
  const uiConvId = useRef<number | null | undefined>(undefined);
  useEffect(() => {
    uiConvId.current = currentConversationId;
  });
  // Subscribe to store for reactive values
  const agentMode = useSyncExternalStore(formAgentStore.subscribe, () => formAgentStore.agentMode);
  const selectedFieldLabel = useSyncExternalStore(formAgentStore.subscribe, () => formAgentStore.getSelectedFieldLabel());
  const selectedFieldHasContent = useSyncExternalStore(formAgentStore.subscribe, () => formAgentStore.getSelectedFieldHasContent());
  const selectedFieldId = useSyncExternalStore(formAgentStore.subscribe, () => formAgentStore.selectedFieldId);
  const currentValues = useSyncExternalStore(formAgentStore.subscribe, () => formAgentStore.currentValues);

  const {
    messages,
    conversations,
    currentConversationId,
    loading,
    streamingContent,
    loadConversations,
    loadMessages,
    newConversation,
    removeConversation,
    addMessage,
    updateLastMessage,
    removeGeneratedFile,
    setCurrentConversationId,
    setStreamingContent,
  } = useChat();

  useEffect(() => {
    if (visible) {
      loadConversations();
    }
  }, [visible, loadConversations]);

  /** 把任务缓存同步进界面：仅当视图正对着任务所属的那条会话 */
  const syncRunView = useCallback(() => {
    const run = runRef.current;
    if (!run || run.convId !== uiConvId.current) return;
    setStreamingContent(run.text);
    streamingRef.current = run.text;
    setAgentSteps(run.steps);
    setSuggestions(run.suggestions);
    setConfirmationCard(run.confirmation);
    setChoiceCard(run.choice);
  }, []);

  /** 视图切到别的会话：把在跑任务的全部实时状态收起来（缓存不动，任务照跑） */
  const hideRunView = useCallback(() => {
    setAgentSteps([]);
    setSuggestions([]);
    setStreamingContent('');
    streamingRef.current = '';
    setConfirmationCard(null);
    setChoiceCard(null);
  }, []);

  /** 任务缓存的唯一写入口：先落缓存，视图对着就同步显示，不对着就只是后台记着 */
  const updateRun = useCallback(
    (patch: Partial<NonNullable<typeof runRef.current>>) => {
      if (!runRef.current) return;
      runRef.current = { ...runRef.current, ...patch };
      syncRunView();
    },
    [syncRunView],
  );

  // 实时状态属于任务自己的会话：切走只是隐藏，切回在跑的那条时接回缓存；
  // 历史消息由 loadMessages 从服务端重取（用户消息发起时已落库，切回不丢）
  const handleSelectConversation = useCallback(
    (id: number) => {
      // 卡片永远只属于发起它的那轮：先无条件收掉，再按归属接回在跑任务的缓存
      setConfirmationCard(null);
      setChoiceCard(null);
      if (runRef.current && runRef.current.convId !== id) hideRunView();
      const promise = loadMessages(id);
      syncRunView();
      return promise;
    },
    [loadMessages, syncRunView, hideRunView],
  );

  const handleNewConversation = useCallback(() => {
    // 新建对话=视图切到「空白会话」（id=null）：同样只隐藏不中断，任务照跑
    setConfirmationCard(null);
    setChoiceCard(null);
    const promise = newConversation();
    if (runRef.current && runRef.current.convId !== null) hideRunView();
    syncRunView();
    return promise;
  }, [newConversation, syncRunView, hideRunView]);

  const handleSend = useCallback(
    (
      content: string,
      attachments?: MessageAttachment[],
      lessonPlanIdOverride?: number | null,
      confirmation?: { confirmationId: number; decision: 'approved' | 'rejected'; feedback?: string },
      // 调用方在同一帧里刚切换过会话时，闭包里的 currentConversationId 是旧值，需显式指定落库会话
      conversationIdOverride?: number | null,
      /** 本条消息是否允许助手联网搜索（输入区开关带过来的） */
      webSearch?: boolean,
    ) => {
      console.log('[ChatWidget] handleSend:', { content: content.substring(0, 30), attachments });

      // 本轮任务归属的会话：之后所有过程态只写进任务缓存，视图只有正对着这条会话时才同步显示。
      // override 分支沿用旧注释：同帧刚切过会话时闭包里的 currentConversationId 是旧值
      const convId = conversationIdOverride === undefined ? currentConversationId : conversationIdOverride;
      uiConvId.current = convId;
      runRef.current = { convId, text: '', steps: [], suggestions: [], confirmation: null, choice: null };
      streamingRef.current = '';
      abortedRef.current = false;
      // Show user message in chat（新任务必然正对着它的会话，直接入列）
      addMessage({
        role: 'user',
        content,
        attachments: attachments && attachments.length > 0 ? attachments : undefined,
      });

      // Always use agent endpoint — it naturally has the right tools based on context
      const capturedFieldId = selectedFieldId;

      formAgentStore.setGenerating(true);
      setAgentLoading(true);
      updateRun({});
      // 用户手动发消息（非确认卡片操作）时，待确认卡片失效，避免陈旧的确认请求；
      // 任何一条新消息发出（含点选项卡片），本轮的选项卡片即完成使命
      if (!confirmation) setConfirmationCard(null);
      setChoiceCard(null);

      const doAgent = async () => {
        const controller = new AbortController();
        agentAbortRef.current = controller;
        let generatedFiles: GeneratedFile[] = [];
        let toolError = '';
        // 本次请求的处理步骤，走 AgentProgress 折叠面板，不再占用消息流
        let steps: ProgressStep[] = [];
        const pushStep = (step: ProgressStep) => {
          // 上一步还在 pending 就标记为完成，保证同一时刻只有一个"进行中"
          steps = steps.map((s) => (s.pending ? { ...s, pending: false } : s));
          steps = [...steps, step];
          updateRun({ steps });
        };
        const resolveStep = (failed = false) => {
          steps = steps.map((s) => (s.pending ? { ...s, pending: false, failed } : s));
          updateRun({ steps });
        };

        await agentChat(
          content,
          agentMode ? capturedFieldId : null,
          agentMode ? formAgentStore.availableFields.map((f) => ({
            id: f.id, label: f.label, type: f.type, width: f.width,
            labelPosition: f.labelPosition, rows: f.rows, group: f.group,
          })) : [],
          agentMode ? formAgentStore.formContext : {},
          agentMode ? formAgentStore.currentValues : {},
          attachments || [],
          {
            onThinking: (text) => {
              // 模型每一步的推理摘要，折进处理面板，不单独占一条消息
              const brief = text.trim().replace(/\s+/g, ' ').slice(0, 40);
              if (brief) pushStep({ kind: 'thinking', label: brief });
            },
            onToolCall: (tool, args) => {
              // args 可能是 { input: '{"fieldId":...}' } 包装，先解出真实参数
              let fieldId = args?.fieldId;
              if (!fieldId && typeof args?.input === 'string') {
                try { fieldId = JSON.parse(args.input)?.fieldId; } catch {}
              }
              // 找不到字段定义时退到「字段」，不能把内部 id 当字段名显示给用户；两课时模板的同名栏带分组名前缀
              const field = formAgentStore.availableFields.find((f) => f.id === fieldId);
              const fieldLabel = field ? (field.group ? `${field.group}·${field.label}` : field.label) : BASIC_FIELD_LABELS[fieldId] || '字段';
              // 键是后端注册的工具名（snake_case，原样进事件），不是前端自己的拼写
              const stepMeta: Record<string, { kind: ProgressStep['kind']; label: string }> = {
                fill_form_field: { kind: 'fillField', label: `正在生成【${fieldLabel}】` },
                search_lesson_plans: { kind: 'searchPlans', label: '正在搜索教案' },
                read_lesson_plan: { kind: 'readPlan', label: '正在读取教案内容' },
                search_knowledge_base: { kind: 'searchFiles', label: '正在搜索资料库' },
                read_knowledge_file: { kind: 'readFile', label: '正在读取文件内容' },
                search_knowledge_chunks: { kind: 'ragSearch', label: '正在检索资料库片段' },
                export_lesson_plan: { kind: 'export', label: '正在导出文件' },
                list_lesson_templates: { kind: 'searchPlans', label: '正在查看可用模板' },
                copy_template: { kind: 'generic', label: '正在拷贝模板' },
                ask_choice: { kind: 'generic', label: '等待你选择' },
                create_lesson_plan: { kind: 'fillField', label: '正在创建教案记录' },
                fill_lesson_plan_fields: { kind: 'fillField', label: '正在生成教案内容' },
                open_lesson_plan_create: { kind: 'generic', label: '正在打开教案新增页' },
                create_ppt_outline: { kind: 'pptOutline', label: '正在规划 PPT 大纲' },
                generate_ppt_slides: { kind: 'pptSlide', label: '正在生成 PPT 页面' },
                update_ppt_slide: { kind: 'pptSlide', label: '正在修改 PPT 页面' },
                search_ppt_reference: { kind: 'searchPlans', label: '正在搜索 PPT 参考资料' },
                search_web: { kind: 'searchFiles', label: '正在联网搜索' },
              };
              const meta = stepMeta[tool] || { kind: 'generic' as const, label: '正在处理' };
              pushStep({ ...meta, pending: true });
            },
            onTool: (toolType, fieldId, toolContent, payload) => {
              if (toolType === 'fill_form_field' && fieldId && toolContent) {
                formAgentStore.fillFormField(fieldId, toolContent);
              } else if (toolType === 'navigate' && typeof payload?.url === 'string') {
                // Agent 跳转指令（如新建教案去表单页）：基础信息先存进跨页 store，新增页挂载后消费；
                // ChatWidget 挂在全局布局，跨页对话不断
                if (payload.basic && typeof payload.basic === 'object') {
                  formAgentStore.setPendingBasic(payload.basic);
                }
                router.push(payload.url);
              }
            },
            onToolResult: (tool, result) => {
              resolveStep(tool === 'export_lesson_plan' && typeof result === 'string' && result.startsWith('错误：'));
              if (tool === 'export_lesson_plan' && typeof result === 'string' && result.startsWith('错误：')) {
                toolError = result;
              }
            },
            onToken: (_token, accumulated) => {
              updateRun({ text: accumulated });
            },
            onFile: (file) => {
              generatedFiles.push(file);
            },
            // 通用确认：关键工具挂起，弹出确认卡片
            onConfirmation: (data) => {
              updateRun({ confirmation: data });
            },
            // askChoice：弹可点击的选项卡片，点选即把该选项发给 Agent
            onChoice: (data) => {
              updateRun({ choice: data });
            },
            onPptSlide: (data) => {
              // 大纲确认后直接进入页面生成时，先初始化预览会话，避免 pushSlide 被忽略
              if (pptPreviewStore.getState().pptId !== data.pptId) {
                pptPreviewStore.beginOutline(data.pptId, '', null);
              }
              pptPreviewStore.pushSlide(data.pptId, data.index, data.slide);
            },
            onPptDone: (data) => {
              pptPreviewStore.finish(data.pptId);
            },
            onSuggestions: (items) => {
              updateRun({ suggestions: items });
            },
            onDone: (data) => {
              formAgentStore.setGenerating(false);
              agentAbortRef.current = null;
              resolveStep();
              const stillHere = uiConvId.current === convId;
              const finalContent = runRef.current?.text ?? '';
              // 有正文就正常落一条助手消息；没有正文（纯工具任务）时给一句结语，
              // 否则对话里只剩折叠面板，用户看不到任何回应。
              // steps 跟着消息一起插入，折叠面板就固定显示在这条回复的上方。
              // 用户已切到别的对话时不插入（服务端已落库，切回该会话时由历史接口取回）
              if (stillHere) {
                if (finalContent) {
                  addMessage({ role: 'assistant', content: finalContent, steps });
                } else if (!toolError) {
                  addMessage({
                    role: 'assistant',
                    content: abortedRef.current
                      ? '已停止生成。'
                      : generatedFiles.length > 0 ? '文件已生成，可以下载了。' : '任务已完成。',
                    steps,
                  });
                }
              }
              // 一轮结束不销毁任务缓存：选项卡/确认卡/引导要在回复之后继续挂着等用户点（askChoice 弹完卡本轮就收尾，
              // 缓存一没了指着 runLive 的卡片会跟着一起消失，用户永远点不到）。
              // 缓存仍只属于这条会话：切走被 hideRunView 收掉，切回由 syncRunView 接回。
              // 正文和步骤已经落成消息，缓存里清掉这两项，切回来不会重复摆一份。
              // 空白对话首发时会话 id 是这一轮才分配下来的，缓存要跟着认领，否则归属判断从此对不上。
              const run = runRef.current;
              if (run) {
                runRef.current = { ...run, convId: data.conversationId ?? run.convId, text: '', steps: [] };
              }
              if (stillHere) {
                setStreamingContent('');
                streamingRef.current = '';
                // 步骤已随消息落库，清掉游离副本，避免面板在消息流底部重复出现
                setAgentSteps([]);
              }
              setAgentLoading(false);
              if (stillHere && generatedFiles.length > 0) {
                updateLastMessage((msg) => ({
                  ...msg,
                  files: [...(msg.files || []), ...generatedFiles],
                }));
              }
              if (data.conversationId) {
                // 任务完成不能把视图跳回它所属的旧会话；用户正看着时才认领
                if (stillHere) setCurrentConversationId(data.conversationId);
                // 后端可能刚新建了这条会话（如空白对话里首次发送），不刷新列表它就不出现在「聊天记录」里
                loadConversations();
              }
            },
            onError: (error) => {
              formAgentStore.setGenerating(false);
              agentAbortRef.current = null;
              setAgentLoading(false);
              resolveStep(true);
              // 出错消息同样只属于任务自己的会话（切回时由历史接口呈现）
              if (uiConvId.current === convId) addMessage({ role: 'assistant', content: `出错了: ${error}`, steps });
              runRef.current = null;
              if (uiConvId.current === convId) setAgentSteps([]);
            },
          },
          convId,
          lessonPlanIdOverride ?? formAgentStore.lessonPlanId,
          pptAgentStore.pptId,
          confirmation ?? null,
          webSearch,
          controller.signal,
        );
      };

      doAgent();
    },
    [
      agentMode,
      selectedFieldId,
      addMessage,
      updateLastMessage,
      currentConversationId,
      setCurrentConversationId,
      loadConversations,
      updateRun,
      syncRunView,
      hideRunView,
    ],
  );

  // 通用确认卡片操作：批准 → 执行挂起工具；拒绝（可带修改意见）→ 模型重新规划；取消 → 仅关卡
  const handleConfirmation = useCallback(
    (decision: 'approved' | 'rejected' | 'cancel', feedback?: string) => {
      const card = confirmationCard;
      setConfirmationCard(null);
      if (decision === 'cancel' || !card) return;
      const message = decision === 'approved' ? '确认执行' : feedback || '请重新调整方案';
      handleSend(message, undefined, null, { confirmationId: card.confirmationId, decision, feedback });
    },
    [confirmationCard, handleSend],
  );

  // 外部页面快捷入口（如教案列表「生成文件」）：只在面板已停在该教案的会话线时原地追加；
  // 其余情况（空白新对话 / 停在别的会话）一律开新会话执行，绝不找回旧会话（10-06 定契约）
  // 按 pending 对象引用去重：StrictMode 下 effect 双跑，第二次拿到的仍是同一快照，会重复消费
  const consumedPendingRef = useRef<{ message: string; lessonPlanId?: number } | null>(null);
  useEffect(() => {
    if (!visible || !pending || consumedPendingRef.current === pending) return;
    consumedPendingRef.current = pending;
    chatUiStore.clearPending();
    const { message, lessonPlanId } = pending;
    const onThisPlanLine =
      !!lessonPlanId && conversations.find((x) => x.id === currentConversationId)?.lessonPlanId === lessonPlanId;
    if (!onThisPlanLine && currentConversationId !== null) handleNewConversation();
    handleSend(message, undefined, lessonPlanId, undefined, onThisPlanLine ? currentConversationId : null);
  }, [visible, pending, conversations, currentConversationId, handleSend, handleNewConversation]);

  // 删除某个生成文件：从所有消息里摘掉对应卡片，刷新后该文件也不再出现
  const handleDeleteFile = useCallback((fileId: number) => {
    removeGeneratedFile(fileId);
  }, [removeGeneratedFile]);

  // 停止生成：掐断 SSE 连接，后端 res close → AbortSignal 会中止在途的模型/工具调用，不再继续计费
  const handleStop = useCallback(() => {
    if (!agentAbortRef.current) return;
    abortedRef.current = true;
    agentAbortRef.current.abort();
  }, []);

  // 回复下方的引导 chips：优先用模型本轮给出的建议（候选与工具同源，点击必能执行）；
  // 建议为空时（历史会话、模型未输出）退回前端按当前上下文派生的一条，等价于旧的输入框上方提示条。
  const chips = (() => {
    if (loading || agentLoading) return [];
    if (suggestions.length > 0) return suggestions;
    if (!agentMode) return [];
    const total = formAgentStore.availableFields.length;
    if (total === 0) return [];
    if (selectedFieldLabel) {
      return [`${selectedFieldHasContent ? '优化' : '生成'}【${selectedFieldLabel}】的内容`];
    }
    const filled = Object.keys(currentValues).length;
    return [filled < total * 0.5 ? '帮我生成整篇教案' : '帮我优化整篇教案'];
  })();

  const handleChip = useCallback((text: string, webSearch?: boolean) => handleSend(text, undefined, undefined, undefined, undefined, webSearch), [handleSend]);

  // 在跑任务的实时状态只在它所属的那条对话里展示；切到别的对话只是隐藏，任务照跑
  const runLive = !!runRef.current && currentConversationId === runRef.current.convId;

  return (    <>
      <ChatBall onClick={() => chatUiStore.toggleVisible()} agentMode={agentMode} hidden={visible} />
      <ChatWindow
        runLive={runLive}
        visible={visible}
        messages={messages}
        loading={loading || agentLoading}
        streamingContent={streamingContent}
        conversations={conversations}
        currentConversationId={currentConversationId}
        agentMode={agentMode}
        selectedFieldLabel={selectedFieldLabel}
        onSend={(message, attachments, webSearch) =>
          handleSend(message, attachments, undefined, undefined, undefined, webSearch)
        }
        onClose={() => chatUiStore.setVisible(false)}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        onDeleteConversation={removeConversation}
        onDeselectField={() => { if (!formAgentStore.isGenerating) formAgentStore.selectField(null); }}
        suggestions={chips}
        onSuggestion={handleChip}
        onStop={handleStop}
        onDeleteFile={handleDeleteFile}
        agentSteps={agentSteps}
        confirmation={confirmationCard}
        onConfirmation={handleConfirmation}
        choice={choiceCard}
        onChoiceSelect={(label) => handleSend(label)}
      />
    </>
  );
}
