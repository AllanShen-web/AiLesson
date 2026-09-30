'use client';

import { useEffect, useState, useMemo, useCallback, useRef, useSyncExternalStore, type ReactNode } from 'react';
import { useSearchParams, useRouter, usePathname } from 'next/navigation';
import {
  Button, Form, Input, Select, Typography, Card, Spin, message, Modal, Space, Tooltip,
} from 'antd';
import { ArrowLeftOutlined, FormOutlined, RobotOutlined } from '@ant-design/icons';
import { ROUTES } from '@/constants/routes';
import {
  templateApi, lessonPlanApi, layoutFieldGrid, splitByDividers, isNoLabel, isStackedLabel, previewBoxHeight, splitLabelLines,
  type TemplateItem, type FormField, type LessonPlanItem,
} from '@/lib/api';
import GradeSelect, { GRADE_TREE } from '@/components/GradeSelect';
import RichTextEditor, { RichTextPreview } from '@/components/RichTextEditor';
import { formAgentStore } from '@/lib/formAgentStore';
import { getIsGenerating } from '@/lib/formAgentStore';
import { chatUiStore } from '@/lib/chatUiStore';

const { Title, Text } = Typography;

/** 多文本编辑器 Modal */
function MultiTextEditorModal({
  visible,
  title,
  value,
  onChange,
  onClose,
}: {
  visible: boolean;
  title: string;
  value: string;
  onChange: (val: string) => void;
  onClose: () => void;
}) {
  // 编辑器内容实时回传，点保存时直接取最新值，避免读编辑器实例的时序问题
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    if (visible) setDraft(value);
  }, [visible, value]);

  const handleOk = () => {
    onChange(draft);
    onClose();
  };

  return (
    <Modal
      title={`编辑：${title}`}
      open={visible}
      onOk={handleOk}
      onCancel={onClose}
      width="80%"
      okText="保存"
      cancelText="取消"
    >
      <RichTextEditor
        value={value}
        onChange={setDraft}
        height={500}
      />
    </Modal>
  );
}

/** 课题位有两套 id：模板编辑页产出的 basic-topic，官方模板的 official-title */
const TOPIC_FIELD_IDS = ['basic-topic', 'official-title'];

/** 课时分组边界：标题形如「第一课时 / 课时1 / 3学时」的纯 label 字段 */
const GROUP_BOUNDARY_RE = /^第?\s*[一二两三四五六七八九十0-9１-９]+\s*(?:课时|学时)/;

/**
 * 识别模板里的课时/分组边界，返回 字段id → 分组名（与服务端 registry.ts 的 assignFieldGroups 同规则）。
 * 边界两种：①「第X课时」这类纯 label；②连续两个及以上分隔符（模板分页，两课时整套字段常这样隔开，
 * 用户把课时标题删了也还剩分页）。边界之后的可填字段归组，最前面的（表头等）不归组。
 */
function computeFieldGroups(fields: FormField[]): Record<string, string> {
  const groups: Record<string, string> = {};
  let current: string | null = null;
  let partSeq = 1;
  let dividerRun = 0;
  for (const f of fields) {
    if (f.type === 'divider') {
      dividerRun += 1;
      continue;
    }
    if (dividerRun >= 2) {
      partSeq += 1;
      current = `第${partSeq}部分`;
    }
    dividerRun = 0;
    if (f.type === 'label') {
      const label = (f.label || '').trim();
      if (GROUP_BOUNDARY_RE.test(label)) {
        current = label;
        partSeq += 1;
      }
      continue;
    }
    if (current) groups[f.id] = current;
  }
  return groups;
}

/** 表头三项：教案自身的课题/学科/年级，不属于模板字段，画布模板里有没有对应栏都不影响 */
const META_TOPIC = 'meta-topic';
const META_SUBJECT = 'meta-subject';
const META_GRADE = 'meta-grade';
/** 自动回填/保存时向画布镜像的字段 id（老模板可能仍带这些基础栏） */
const CANVAS_IDS_FOR: Record<BasicFillId, string[]> = {
  'basic-unit': ['basic-unit'],
  'basic-subject': ['basic-subject', 'official-subject'],
  'basic-grade': ['basic-grade', 'official-grade'],
};

/** 取第一个非空值：自动回填和助手上下文都要兼容两套 id 体系 */
function firstFilled(values: Record<string, any>, ids: string[]): string {
  for (const id of ids) {
    const v = values[id];
    if (v !== undefined && v !== null && String(v).trim() !== '') return String(v);
  }
  return '';
}

// 自动回填：只填这三项，课题名是输入源本身
const BASIC_FILL_IDS = ['basic-unit', 'basic-subject', 'basic-grade'] as const;
type BasicFillId = (typeof BASIC_FILL_IDS)[number];
const BASIC_FILL_META: Record<BasicFillId, { label: string; key: 'unit' | 'subject' | 'grade' }> = {
  'basic-unit': { label: '单元', key: 'unit' },
  'basic-subject': { label: '学科', key: 'subject' },
  'basic-grade': { label: '年级', key: 'grade' },
};
/** 年级树叶子值：模型给的年级必须逐字命中，否则丢弃 */
const GRADE_VALUES = GRADE_TREE.flatMap((node) => (node.children || []).map((child) => child.value));

/** 按课题名挑一条历史教案：标题完全相同优先，其次互为包含（副本、书名号都能命中） */
function pickHistoryPlan(plans: LessonPlanItem[], topic: string): LessonPlanItem | null {
  const norm = (s: string) => (s || '').replace(/[《》\s]/g, '').toLowerCase();
  const t = norm(topic);
  if (!t) return null;
  return plans.find((p) => norm(p.title) === t) || plans.find((p) => norm(p.title).includes(t)) || null;
}

function renderField(field: FormField) {
  if (field.id === 'basic-grade') return <GradeSelect style={{ width: '100%' }} />;

  switch (field.type) {
    case 'textarea':
      // 原地可编辑：Form.Item 会注入 value/onChange，失焦时清洗后的 HTML 直接回写表单
      return <RichTextPreview rows={field.rows} editable />;
    case 'select': 
      return <Select placeholder={field.placeholder || '请选择'} options={(field.options || []).map((o) => ({ label: o, value: o }))} />;
    case 'number': 
      return <Input type="number" placeholder={field.placeholder} />;
    default: 
      return <Input placeholder={field.placeholder} />;
  }
}

/** label 在左还是在顶由模板字段设置决定；在顶与无标签都不带 labelCol/wrapperCol，否则 antd 仍按横向栅格分宽 */
function fieldItemLayout(field: FormField) {
  return isStackedLabel(field)
    ? { layout: 'vertical' as const }
    : {
        layout: 'horizontal' as const,
        labelCol: { style: { width: 100, whiteSpace: 'normal', lineHeight: '22px' } },
        wrapperCol: { style: { flex: 1 } },
      };
}

/** 富文本字段的操作按钮组容器：标签在上或无标签=框右上角横排；标签在左=框外右侧竖排 */
function FieldFabGroup({ side, children }: { side: boolean; children: ReactNode }) {
  return <div className={side ? 'field-fabs field-fabs-side' : 'field-fabs'}>{children}</div>;
}

/** 富文本字段上的编辑按钮：桌面端悬停字段区才显示，避免遮挡正文 */
function FieldEditButton({ field, onEdit }: { field: FormField; onEdit: (field: FormField) => void }) {
  return (
    <Button
      className="field-fab"
      type="text"
      size="small"
      shape="circle"
      icon={<FormOutlined />}
      onClick={(e) => {
        e.stopPropagation();
        onEdit(field);
      }}
      title="富文本编辑"
      style={{ color: '#1677ff', background: '#fff', boxShadow: '0 1px 4px rgba(0, 0, 0, 0.14)' }}
    />
  );
}

/** 富文本字段上的 AI 一键生成按钮：桌面端悬停才显示 */
function FieldAiButton({ field, onGenerate }: { field: FormField; onGenerate: (field: FormField) => void }) {
  return (
    <Button
      className="field-fab"
      type="text"
      size="small"
      shape="circle"
      icon={<RobotOutlined />}
      onClick={(e) => {
        e.stopPropagation();
        onGenerate(field);
      }}
      title="AI 生成该字段"
      style={{ color: '#667eea', background: '#fff', boxShadow: '0 1px 4px rgba(0, 0, 0, 0.14)' }}
    />
  );
}

function AgentFieldWrapper({
  fieldId,
  selectedFieldId,
  isGenerating,
  selectable = true,
  stickySelect = false,
  onSelect,
  children,
}: {
  fieldId: string;
  selectedFieldId: string | null;
  isGenerating?: boolean;
  selectable?: boolean;
  /** 原地编辑字段：点击是为了放光标，只选中、不再按次取消，否则点一下编辑紫框就没了 */
  stickySelect?: boolean;
  onSelect: (id: string | null) => void;
  children: React.ReactNode;
}) {
  const isSelected = selectedFieldId === fieldId;
  return (
    <div
      onClick={(e) => {
        e.stopPropagation();
        // 助手代填不了的字段（课题位）不给选中，避免出现紫框却没得填
        if (!selectable) return;
        // 生成中：只允许取消当前选中，不允许切换到其他字段
        if (isGenerating && !isSelected) return;
        onSelect(isSelected && !stickySelect ? null : fieldId);
      }}
      style={{
        padding: 4,
        borderRadius: 8,
        border: isSelected ? '2px solid #1677ff' : '2px solid transparent',
        background: isSelected ? 'rgba(22, 119, 255, 0.05)' : 'transparent',
        cursor: 'pointer',
        transition: 'border-color 0.15s, background 0.15s',
        marginBottom: -4,
      }}
    >
      {children}
    </div>
  );
}

export default function CreateLessonPlanPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const templateId = searchParams.get('templateId');
  const editId = searchParams.get('id');
  const store = formAgentStore;

  // Exit agent mode when component unmounts (navigating away)
  useEffect(() => {
    return () => {
      console.log('[Agent] Create page unmounting, clearing agent mode');
      store.clearFields();
    };
  }, []);

  // Subscribe to store changes for reactive values
  const selectedFieldId = useSyncExternalStore(
    store.subscribe,
    () => store.selectedFieldId,
    () => store.selectedFieldId,
  );

  const isGenerating = useSyncExternalStore(
    store.subscribe,
    getIsGenerating,
    getIsGenerating,
  );

  // 助手停靠分栏时正文列已变窄，表单若仍按 70% 收窄会剩下一大片空白
  const chatDocked = useSyncExternalStore(
    chatUiStore.subscribe,
    () => chatUiStore.isDocked(),
    () => false,
  );
  const formMaxWidth = chatDocked ? '100%' : '70%';

  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [template, setTemplate] = useState<TemplateItem | null>(null);
  
  // 多文本编辑器状态
  const [editorModal, setEditorModal] = useState<{ visible: boolean; fieldId: string; label: string }>({ 
    visible: false, 
    fieldId: '', 
    label: '' 
  });

  useEffect(() => { document.title = editId ? '编辑教案' : '新建教案'; }, [editId]);

  /** 展开富文本编辑：stopPropagation 会让字段选中逻辑失效，所以这里显式选中当前字段 */
  const openRichTextEditor = (field: FormField) => {
    // 与 AgentFieldWrapper 同一条规则：生成中不允许把选中态切到别的字段
    if (!isGenerating || selectedFieldId === field.id) store.selectField(field.id);
    setEditorModal({ visible: true, fieldId: field.id, label: field.label });
  };

  /** 一键 AI 生成：选中该字段并让助手生成/优化内容；激活助手依赖表头课题，没激活时先拦住 */
  const generateFieldByAgent = (field: FormField) => {
    if (!store.agentMode) { message.warning('请先在基本信息里填写课题，激活助手后才能一键生成'); return; }
    if (isGenerating) return;
    store.selectField(field.id);
    const v = form.getFieldValue(field.id);
    const action = v !== undefined && v !== null && String(v) !== '' ? '优化' : '生成';
    chatUiStore.requestSend(`${action}${field.label}的内容`);
  };

  // loading 或未找到模板时页面整块提前 return，Form 还没挂载，这期间直接调 form 的方法会撞上
  // antd「Instance created by useForm is not connected to any Form element」告警，所以先攒着等挂载后回填
  const pendingFieldValues = useRef<Record<string, unknown>>({});

  useEffect(() => {
    const tid = templateId || editId;
    if (!tid) { setLoading(false); return; }
    if (editId) {
      store.setLessonPlanId(Number(editId));
      lessonPlanApi.getById(Number(editId))
        .then((plan) => {
          Object.assign(pendingFieldValues.current, {
            [META_TOPIC]: plan.title,
            [META_SUBJECT]: plan.subject,
            [META_GRADE]: plan.grade,
          });
          if (plan.templateId) {
            templateApi.getById(plan.templateId).then((tpl) => {
              setTemplate(tpl);
              const vals = plan.content?.values || {};
              const fieldValues: Record<string, unknown> = {};
              for (const field of tpl.content?.fields || []) {
                if (vals[field.id] !== undefined) fieldValues[field.id] = vals[field.id];
              }
              Object.assign(pendingFieldValues.current, fieldValues);
            });
          }
        })
        .catch(() => message.error('加载教案失败'))
        .finally(() => setLoading(false));
    } else {
      // Agent 跳转带来的基础信息预填：课题/学科直填，年级必须逐字命中表头选项才收，其余留给下方自动回填链路
      const basic = store.consumePendingBasic();
      if (basic) {
        const prefill: Record<string, unknown> = {};
        if (basic.topic) prefill[META_TOPIC] = basic.topic;
        if (basic.subject) prefill[META_SUBJECT] = basic.subject;
        if (basic.grade && GRADE_VALUES.includes(basic.grade)) prefill[META_GRADE] = basic.grade;
        Object.assign(pendingFieldValues.current, prefill);
      }
      templateApi.getById(Number(templateId))
        .then((tpl) => setTemplate(tpl))
        .catch(() => message.error('加载模板失败'))
        .finally(() => setLoading(false));
    }
  }, [templateId, editId]);

  const formMounted = !loading && !!template;
  useEffect(() => {
    if (!formMounted) return;
    const pending = pendingFieldValues.current;
    pendingFieldValues.current = {};
    if (Object.keys(pending).length > 0) form.setFieldsValue(pending);
  }, [formMounted, form]);

  const templateFields = useMemo(() => template?.content?.fields || [], [template]);
  /** 助手可代填的字段：课题位（模板里唯一锁定项）、纯标签与分隔符（都没得填）不交给模型，单元/学科/年级随普通字段交给助手。
   *  注册前按模板完整结构（含标签/分隔符）算好课时分组附带进 group，助手才分得清两课时的同名栏 */
  const fillableFields = useMemo(() => {
    const groups = computeFieldGroups(templateFields);
    return templateFields
      .filter((f) => !TOPIC_FIELD_IDS.includes(f.id) && f.type !== 'label' && f.type !== 'divider')
      .map((f) => (groups[f.id] ? { ...f, group: groups[f.id] } : f));
  }, [templateFields]);

  // AI 生成教案：课题/学科/年级是生成的依据，三项齐了才让点
  const metaTopic = Form.useWatch(META_TOPIC, form);
  const metaSubject = Form.useWatch(META_SUBJECT, form);
  const metaGrade = Form.useWatch(META_GRADE, form);
  const metaReady = [metaTopic, metaSubject, metaGrade].every((v) => String(v || '').trim() !== '');

  // 画布里的课题位是模板锁定栏（不交给助手填），保存时才由表头课题覆盖。
  // 生成过程中它一直空着像是漏填，所以表头一改就实时镜像进去，与保存结果同值。
  useEffect(() => {
    const topicField = templateFields.find((f) => TOPIC_FIELD_IDS.includes(f.id));
    const topic = String(metaTopic || '').trim();
    if (!topicField || !topic) return;
    if (String(form.getFieldValue(topicField.id) || '').trim() !== topic) {
      form.setFieldValue(topicField.id, topic);
    }
  }, [metaTopic, templateFields, form]);

  const handleAgentGenerate = useCallback(() => {
    const send = () => chatUiStore.requestSend('帮我生成整篇教案');
    // 只提醒多行正文会被重写：下拉/单行这些短字段模型顺手就改回来了，不值得拦一道
    const hasBody = templateFields.some(
      (f) => f.type === 'textarea' && String(form.getFieldValue(f.id) || '').trim() !== '',
    );
    if (!hasBody) {
      send();
      return;
    }
    Modal.confirm({
      title: '当前内容会重新生成，确认吗',
      content: '助手会按课题、学科、年级重写教案正文，已填写的多行内容会被覆盖。',
      okText: '确认生成',
      cancelText: '取消',
      onOk: send,
    });
  }, [form, templateFields]);

  // 表头课题填好即可用助手：学科/年级允许空着，画布里有没有对应栏也不影响激活
  const prevActiveRef = useRef(false);
  const syncAgentFromForm = useCallback(() => {
    const values = form.getFieldsValue();
    const topic = String(values[META_TOPIC] || '').trim() || firstFilled(values, TOPIC_FIELD_IDS);
    const shouldActivate = !!topic && templateFields.length > 0;
    if (shouldActivate !== prevActiveRef.current) {
      prevActiveRef.current = shouldActivate;
      console.log('[Agent] agentMode →', shouldActivate);
      if (shouldActivate) store.registerFields(fillableFields);
      else store.clearFields();
    }
    if (!shouldActivate) return;
    store.setFormContext({
      topic,
      unit: firstFilled(values, ['basic-unit']),
      subject: String(values[META_SUBJECT] || '').trim() || firstFilled(values, CANVAS_IDS_FOR['basic-subject']),
      grade: String(values[META_GRADE] || '').trim() || firstFilled(values, CANVAS_IDS_FOR['basic-grade']),
    });
    // 同步当前教案内容到 store（供 AI 感知已有内容）
    const contentValues: Record<string, string> = {};
    for (const field of fillableFields) {
      if (values[field.id] !== undefined && values[field.id] !== '') {
        contentValues[field.id] = String(values[field.id]);
      }
    }
    store.setCurrentValues(contentValues);
  }, [templateFields, fillableFields, form]);

  // Re-check on every render when template is loaded
  useEffect(() => {
    if (!loading && templateFields.length > 0) {
      syncAgentFromForm();
      scheduleBasicFill();
    }
  });

  // 填了课题名就自动回填单元/学科/年级：先查同名历史教案，查不到再让模型推断
  const pageAlive = useRef(true);
  const basicFillTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const basicFilledTopic = useRef('');
  useEffect(() => {
    pageAlive.current = true;
    return () => {
      pageAlive.current = false;
      if (basicFillTimer.current) clearTimeout(basicFillTimer.current);
    };
  }, []);

  /** 还能填的字段：表头或画布任一有值就算填好了（不管是手填的还是上次记忆带出来的），一律不再回填；
   *  单元没有表头位，模板里没这栏就算了 */
  const fillableBasicIds = useCallback((values: Record<string, any>): BasicFillId[] => {
    const valueIdsFor = (id: BasicFillId) =>
      id === 'basic-subject' ? [META_SUBJECT, ...CANVAS_IDS_FOR[id]]
        : id === 'basic-grade' ? [META_GRADE, ...CANVAS_IDS_FOR[id]]
        : CANVAS_IDS_FOR[id];
    return BASIC_FILL_IDS.filter((id) => {
      // 学科/年级永远有表头可填；单元没有表头位，模板里没这栏就算了
      if (id === 'basic-unit' && !templateFields.some((f) => CANVAS_IDS_FOR['basic-unit'].includes(f.id))) return false;
      return !firstFilled(values, valueIdsFor(id));
    });
  }, [templateFields]);

  /** 模板里是下拉的字段把候选交给模型逐字选；纯输入框允许自由文本 */
  const basicFillOptions = useCallback(() => {
    const selectOptions = (id: string) => {
      const f = templateFields.find((item) => item.id === id);
      return f && f.type === 'select' ? f.options || [] : [];
    };
    return { subject: selectOptions('basic-subject'), grade: GRADE_VALUES, unit: selectOptions('basic-unit') };
  }, [templateFields]);

  const runBasicFill = useCallback(async (topic: string, ids: BasicFillId[]) => {
    const picked: Partial<Record<BasicFillId, string>> = {};
    // 实时口径：回填途中用户随时可以自己填，每一步都只处理当前还空着的项
    const openIds = () => new Set(fillableBasicIds(form.getFieldsValue()));
    const take = (src: Partial<Record<'unit' | 'subject' | 'grade', string | number>> | null | undefined) => {
      const open = openIds();
      for (const id of ids) {
        if (picked[id] || !src || !open.has(id)) continue;
        const raw = src[BASIC_FILL_META[id].key];
        const v = typeof raw === 'string' ? raw.trim() : '';
        if (v) picked[id] = v;
      }
    };

    try {
      let hit = pickHistoryPlan(await lessonPlanApi.getMyAll({ keyword: topic }), topic);
      if (!hit) hit = pickHistoryPlan(await lessonPlanApi.getPublicAll({ keyword: topic }), topic);
      if (hit) take({ unit: hit.content?.values?.['basic-unit'], subject: hit.subject, grade: hit.grade });
    } catch {}
    const fromHistory = Object.keys(picked).length > 0;

    if (ids.some((id) => !picked[id] && openIds().has(id))) {
      try {
        take(await lessonPlanApi.predictBasic({ topic, options: basicFillOptions() }));
      } catch {}
    }

    if (!pageAlive.current) return;
    const open = openIds();
    const applied = (Object.keys(picked) as BasicFillId[]).filter((id) => open.has(id));
    if (applied.length === 0) return;
    const applyVals: Record<string, string> = {};
    for (const key of applied) {
      const v = picked[key]!;
      if (key === 'basic-subject') applyVals[META_SUBJECT] = v;
      if (key === 'basic-grade') applyVals[META_GRADE] = v;
      // 画布上有对应栏就一并填上（只补空位，不覆盖手填），没有就算了
      for (const cid of CANVAS_IDS_FOR[key]) {
        if (templateFields.some((f) => f.id === cid) && !form.getFieldValue(cid)) applyVals[cid] = v;
      }
    }
    form.setFieldsValue(applyVals);
    syncAgentFromForm();
    const labels = applied.map((id) => BASIC_FILL_META[id].label).join('、');
    message.info(`已自动回填${labels}（${fromHistory ? '同名教案' : 'AI 推断'}）`);
  }, [form, templateFields, syncAgentFromForm, basicFillOptions, fillableBasicIds]);

  const scheduleBasicFill = useCallback(() => {
    if (editId) return;
    const values = form.getFieldsValue();
    const topic = String(values[META_TOPIC] || '').trim() || firstFilled(values, TOPIC_FIELD_IDS);
    if (!topic) return;
    if (fillableBasicIds(values).length === 0 || basicFilledTopic.current === topic) return;
    basicFilledTopic.current = topic;
    if (basicFillTimer.current) clearTimeout(basicFillTimer.current);
    // 停顿 1s 才触发：单字课文名（如《春》）也要能回填，所以不按长度拦，只按输入停顿拦
    basicFillTimer.current = setTimeout(() => {
      // 这一秒里用户可能刚把空着的几项填上，那就整条流程都不启动，一次 AI 都不调
      const ids = fillableBasicIds(form.getFieldsValue());
      if (ids.length > 0) runBasicFill(topic, ids);
    }, 1000);
  }, [editId, form, fillableBasicIds, runBasicFill]);

  // 输入即判定：表头三项和画布字段都在这一个 Form 里，值一变就同步助手上下文并尝试自动回填
  const handleValuesChange = useCallback(() => {
    syncAgentFromForm();
    scheduleBasicFill();
  }, [syncAgentFromForm, scheduleBasicFill]);

  // Listen for agent fill events
  useEffect(() => {
    const handler = (e: Event) => {
      const { fieldId, value } = (e as CustomEvent).detail;
      console.log('[Agent] page received fill event:', fieldId, value?.length);
      form.setFieldsValue({ [fieldId]: value });
      // 同步更新 store 中的当前内容
      const current = { ...store.currentValues, [fieldId]: value };
      store.setCurrentValues(current);
    };
    window.addEventListener('agent:fillField', handler);
    return () => window.removeEventListener('agent:fillField', handler);
  }, [form]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    const title = String(values[META_TOPIC] || '').trim() || firstFilled(values, TOPIC_FIELD_IDS) || firstFilled(values, ['basic-unit']) || '未命名教案';
    const subject = String(values[META_SUBJECT] || '').trim() || firstFilled(values, CANVAS_IDS_FOR['basic-subject']);
    const grade = String(values[META_GRADE] || '').trim() || firstFilled(values, CANVAS_IDS_FOR['basic-grade']);
    const fieldValues: Record<string, string | number> = {};
    for (const field of templateFields) {
      let v = values[field.id];
      // 画布有对应空栏时拿表头值镜像过去，导出那侧才有内容可取；没有就算了
      if (v === undefined || v === null || v === '') {
        if (TOPIC_FIELD_IDS.includes(field.id)) v = title;
        else if (CANVAS_IDS_FOR['basic-subject'].includes(field.id)) v = subject;
        else if (CANVAS_IDS_FOR['basic-grade'].includes(field.id)) v = grade;
      }
      if (v !== undefined && v !== null && v !== '') {
        fieldValues[field.id] = v;
      }
    }
    const content = { fields: templateFields, values: fieldValues };
    setSaving(true);
    try {
      if (editId) {
        await lessonPlanApi.update(Number(editId), { title, content, subject, grade });
        message.success('教案保存成功');
      } else {
        const result = await lessonPlanApi.create({ title, templateId: Number(templateId), content, subject, grade });
        message.success('教案保存成功');
        // 新建后切换为编辑模式，不跳转
        if (result?.id) {
          router.replace(`${pathname}?id=${result.id}`);
        }
      }
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

  if (!template) {
    return (
      <div style={{ textAlign: 'center', padding: 60 }}>
        <Text type="secondary">未找到模板，请从教案列表重新创建</Text>
        <br />
        <Button type="link" onClick={() => router.push(ROUTES.LESSON_PLANS)}>返回列表</Button>
      </div>
    );
  }

  return (
    <div className="lesson-plan-page-root" onClick={() => store.selectField(null)}>
      {/* 表头跟表单卡片同宽，否则助手没停靠时保存按钮会顶到页面最右边，和卡片右沿错开 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', maxWidth: formMaxWidth, marginBottom: 12 }}>
        <Space>
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => router.push(ROUTES.LESSON_PLANS)} style={{ paddingLeft: 0 }}>返回</Button>
          <Title level={4} style={{ margin: 0 }}>{editId ? '编辑教案' : '新建教案'}</Title>
        </Space>
        <Button type="primary" htmlType="submit" loading={saving} size="large" form="lesson-plan-form">
          保存教案
        </Button>
      </div>

      <style>{`
        /* AI 生成键用助手同款渐变，和蓝色主色控件区分开；禁用态交回 antd 默认样式 */
        .ai-generate-btn:not(:disabled) {
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          border-color: transparent;
          color: #fff;
        }
        .ai-generate-btn:not(:disabled):hover {
          background: linear-gradient(135deg, #7b8cf0 0%, #8a5cb5 100%);
          border-color: transparent;
          color: #fff;
        }
        /* 按钮组定位：标签在顶=框右上角横排；标签在左=框外右侧竖排（单元格已留 34px 边距） */
        .field-fabs { position: absolute; top: 6px; right: 8px; z-index: 1; display: flex; gap: 6px; }
        .field-fabs-side { top: 50%; right: -18px; transform: translateY(-50%); flex-direction: column; }
        /* 桌面端：根节点填满 Content（顶栏/边距已由外层 Layout 扣掉），按钮行与基本信息固定，
           教案内容卡吃剩余高度并在卡片内部滚动，页面本身不出滚动条；窄屏退回页面滚动 */
        @media (min-width: 769px) {
          .lesson-plan-page-root { height: 100%; display: flex; flex-direction: column; }
          .lesson-plan-form-layout { flex: 1; min-height: 0; display: flex; flex-direction: column; }
          .lesson-plan-form-layout .meta-card { flex-shrink: 0; }
          .lesson-plan-form-layout .canvas-card { flex: 1; min-height: 0; display: flex; flex-direction: column; }
          .lesson-plan-form-layout .canvas-card .ant-card-body { flex: 1; min-height: 0; overflow-y: auto; }
          /* 悬浮按钮默认藏起，悬停字段区才显示，避免盖住正文首行；触屏无 hover，窄屏保持常驻 */
          .field-cell .field-fab { opacity: 0; pointer-events: none; }
          .field-cell:hover .field-fab { opacity: 1; pointer-events: auto; }
          .field-cell .field-fab { transition: opacity 0.15s; }
        }
      `}</style>
      <Form id="lesson-plan-form" className="lesson-plan-form-layout" form={form} onFinish={handleSubmit} onValuesChange={handleValuesChange} onClick={(e) => e.stopPropagation()} validateTrigger={['onSubmit']}>
        {/* 基本信息表头：课题/学科/年级属于教案本身，不属于模板；浅蓝底和白色教案内容区分 */}
        <Card className="meta-card" style={{ maxWidth: formMaxWidth, marginBottom: 12, background: '#f0f5ff', borderColor: '#d6e4ff' }} styles={{ body: { padding: '12px 16px' } }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <Form.Item name={META_TOPIC} label="课题" rules={[{ required: true, message: '请填写课题' }]} style={{ marginBottom: 0, flex: '2 1 200px', minWidth: 148 }}>
              <Input placeholder="请输入课题" />
            </Form.Item>
            <Form.Item name={META_SUBJECT} label="学科" style={{ marginBottom: 0, flex: '1 1 140px', minWidth: 116 }}>
              <Input placeholder="如：语文" />
            </Form.Item>
            {/* 年级和 AI 键绑成一组：窄一点时两者一起换到第二行，不会出现按钮孤零零占一行 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '1 1 200px', minWidth: 184 }}>
              <Form.Item name={META_GRADE} label="年级" style={{ marginBottom: 0, flex: '1 1 0', minWidth: 0 }}>
                <GradeSelect style={{ width: '100%' }} />
              </Form.Item>
              {/* disabled 的按钮不响应 hover，Tooltip 要包一层 span 才拿得到悬浮文字 */}
              <Tooltip title="AI 生成教案">
                <span style={{ display: 'inline-flex', flexShrink: 0 }}>
                  <Button
                    className="ai-generate-btn"
                    icon={<RobotOutlined />}
                    disabled={!metaReady || isGenerating}
                    loading={isGenerating}
                    onClick={handleAgentGenerate}
                  />
                </span>
              </Tooltip>
            </div>
          </div>
        </Card>

        <Card className="canvas-card" style={{ maxWidth: formMaxWidth }}>
          {/* 分隔符把内容切成几张独立的表：每段一个 12 列网格，段间按分隔符个数画横线；
              段内仍是 12 列网格 + 行号列号摆放，与画布/详情/导出同一套 layoutFieldGrid 口径，
              贯穿标题用 grid-row span 天然占住下面几行的行首 */}
          {(() => {
            const grids: ReactNode[] = [];
            for (const segment of splitByDividers(templateFields)) {
              // 分隔符在页面上就是横线：连着几个画几条，让「这里断开成另一张表」看得见
              if (segment.dividerCount > 0) {
                grids.push(
                  <div
                    key={`sep-${segment.fields[0].id}`}
                    style={{ display: 'flex', flexDirection: 'column', gap: 5, margin: '18px 0' }}
                  >
                    {Array.from({ length: segment.dividerCount }, (_, i) => (
                      <div key={i} style={{ borderTop: '1px solid #d9d9d9' }} />
                    ))}
                  </div>,
                );
              }
              const cells: ReactNode[] = [];
              layoutFieldGrid(segment.fields).forEach((row, ri) => {
                let col = row.covered;
                for (const cell of row.cells) {
                  const { field } = cell;
                  const pos = { gridColumn: `${col + 1} / span ${cell.colSpan}`, gridRow: `${ri + 1} / span ${cell.rowSpan}` };
                  col += cell.colSpan;
                  cells.push(
                    field.type === 'label' ? (
                      // 纯栏目标题：没有输入控件，不进表单也不参与助手选中；
                      // 折行与最小高度都和导出单元格同一口径（1/12 一字一行、rows 撑高；贯穿格随行高拉伸）
                      <div
                        key={field.id}
                        style={{
                          ...pos,
                          padding: 4,
                          fontWeight: 600,
                          background: '#fafafa',
                          borderRadius: 8,
                          fontSize: 13,
                          whiteSpace: 'pre-line',
                          textAlign: 'center',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          minHeight: cell.rowSpan > 1 ? undefined : field.rows ? previewBoxHeight(field.rows, true) : undefined,
                        }}
                      >
                        {splitLabelLines(field.label, cell.colSpan / 12).join('\n')}
                      </div>
                    ) : (
                      <div key={field.id} style={pos}>
                        <AgentFieldWrapper
                          fieldId={field.id}
                          selectedFieldId={selectedFieldId}
                          isGenerating={isGenerating}
                          selectable={!TOPIC_FIELD_IDS.includes(field.id)}
                          stickySelect={field.type === 'textarea'}
                          onSelect={store.selectField}
                        >
                          <div className="field-cell" style={{ position: 'relative', paddingRight: field.type === 'textarea' && !isStackedLabel(field) ? 5 : undefined }}>
                            {field.type === 'textarea' && (
                              <FieldFabGroup side={!isStackedLabel(field)}>
                                <FieldAiButton field={field} onGenerate={generateFieldByAgent} />
                                <FieldEditButton field={field} onEdit={openRichTextEditor} />
                              </FieldFabGroup>
                            )}
                            <Form.Item
                              name={field.id}
                              label={isNoLabel(field) ? undefined : field.label}
                              {...fieldItemLayout(field)}
                              style={{ marginBottom: 0 }}
                            >
                              {renderField(field)}
                            </Form.Item>
                          </div>
                        </AgentFieldWrapper>
                      </div>
                    ),
                  );
                }
              });
              grids.push(
                <div
                  key={segment.fields[0].id}
                  style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: 16 }}
                >
                  {cells}
                </div>,
              );
            }
            return grids;
          })()}
        </Card>
      </Form>

      {/* 多文本编辑器 Modal - 用 open 控制挂载,关闭时完全卸载 */}
      {editorModal.visible && (
        <MultiTextEditorModal
          visible={editorModal.visible}
          title={editorModal.label}
          value={form.getFieldValue(editorModal.fieldId) || ''}
          onChange={(val) => {
            form.setFieldsValue({ [editorModal.fieldId]: val });
            // 同步更新 store
            const current = { ...store.currentValues, [editorModal.fieldId]: val };
            store.setCurrentValues(current);
          }}
          onClose={() => setEditorModal({ visible: false, fieldId: '', label: '' })}
        />
      )}
    </div>
  );
}
