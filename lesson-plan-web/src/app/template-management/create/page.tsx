'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import {
  Button,
  Input,
  Space,
  Typography,
  message,
  Card,
  Empty,
  Spin,
  Form,
  Slider,
  Segmented,
  Alert,
  Tooltip,
} from 'antd';
import {
  ArrowLeftOutlined,
  SaveOutlined,
  DeleteOutlined,
  HolderOutlined,
  FormOutlined,
  AlignLeftOutlined,
  DownSquareOutlined,
  NumberOutlined,
  FontSizeOutlined,
  BorderHorizontalOutlined,
  CaretDownOutlined,
} from '@ant-design/icons';
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
  DragOverlay,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { templateApi, fieldWidth, isTopLabel, isNoLabel, previewBoxHeight, splitLabelLines, layoutFieldGrid, type TemplateItem, type FormField, type FormSchema, type FieldGridRow } from '@/lib/api';
import { ROUTES, TEMPLATE_IMPORT_DRAFT_KEY } from '@/constants/routes';
import GradeSelect from '@/components/GradeSelect';

const { Title, Text } = Typography;

const FIELD_TYPES: { type: FormField['type']; label: string; icon: React.ReactNode }[] = [
  { type: 'input', label: '单行输入', icon: <FormOutlined /> },
  { type: 'textarea', label: '多行文本', icon: <AlignLeftOutlined /> },
  { type: 'select', label: '下拉选择', icon: <DownSquareOutlined /> },
  { type: 'number', label: '数字输入', icon: <NumberOutlined /> },
  { type: 'label', label: '纯标签', icon: <FontSizeOutlined /> },
  { type: 'divider', label: '分隔符', icon: <BorderHorizontalOutlined /> },
];

const FIELD_TYPE_MAP: Record<FormField['type'], string> = {
  input: '单行输入',
  textarea: '多行文本',
  select: '下拉选择',
  number: '数字输入',
  label: '纯标签',
  divider: '分隔符',
};

/** 画布上六类字段用底色区分，不再显示类型文字 */
const FIELD_TYPE_BG: Record<FormField['type'], string> = {
  input: '#e6f4ff',
  textarea: '#f6ffed',
  select: '#fff7e6',
  number: '#f9f0ff',
  label: '#fafafa',
  divider: '#f0f0f0',
};

/** 类型色的图标版：面板/切换器里图标用同色系深一档，跟底色一眼对上 */
const FIELD_TYPE_COLOR: Record<FormField['type'], string> = {
  input: '#1677ff',
  textarea: '#52c41a',
  select: '#fa8c16',
  number: '#722ed1',
  label: '#8c8c8c',
  divider: '#595959',
};

/** 被省略的标题/占位字符等信息收进悬浮详情 */
function fieldDetailText(field: FormField): string {
  if (field.type === 'divider') {
    return '分隔符：把它上下的字段各成一张独立的表；紧挨着放两个则在这一行断开并换页';
  }
  const widthCol = Math.round(fieldWidth(field) * 12);
  const parts = [`${field.label}（${FIELD_TYPE_MAP[field.type]}）`];
  if (field.type === 'label') {
    if ((field.rowSpan ?? 1) > 1) parts.push(`贯穿 ${field.rowSpan} 行`);
    if (field.rows) parts.push(`高度 ${field.rows} 行`);
  } else {
    parts.push(isNoLabel(field) ? '无标签' : isTopLabel(field) ? '标签在上' : '标签在左');
    if (field.placeholder) parts.push(`占位：${field.placeholder}`);
    if (field.type === 'textarea') parts.push(`高度 ${field.rows ?? 4} 行`);
    if (field.type === 'select' && field.options?.length) parts.push(`选项：${field.options.join('、')}`);
  }
  parts.push(`宽度 ${widthCol === 12 ? '全宽' : twelfthsLabel(widthCol)}`);
  return parts.join('｜');
}

/** 十二档 → 约分文字（3 → 1/4，5 → 5/12） */
function twelfthsLabel(n: number): string {
  const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b));
  const g = gcd(n, 12);
  return g === 1 ? `${n}/12` : `${n / g}/${12 / g}`;
}

let dragCounter = 0;

interface ImportDraft {
  name: string;
  fields: FormField[];
  sourceName: string;
}

/**
 * 读取 Word 导入的草稿。只读不删：开发模式下同一个 effect 会跑两次，
 * 读完就删会让第二次拿到空字段，页面看起来"导入没生效"。
 * 保存成功后再由 handleSave 清掉。
 */
function readImportDraft(): ImportDraft | null {
  try {
    const raw = sessionStorage.getItem(TEMPLATE_IMPORT_DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed?.fields)) return null;
    return {
      name: String(parsed.name ?? ''),
      fields: parsed.fields as FormField[],
      sourceName: String(parsed.sourceName ?? ''),
    };
  } catch {
    return null;
  }
}

/**
 * 画布上的一块：一段字段摆成一张独立的 12 列网格，分隔符单独成条（它不属于任何一张表）。
 */
type CanvasBlock =
  | { kind: 'divider'; field: FormField; pageBreak: boolean }
  | { kind: 'grid'; key: string; rows: FieldGridRow<FormField>[]; rowHeights: number[] };

function SortableField({
  field,
  colSpan,
  gridStyle,
  heightPx,
  pageBreak,
  isSelected,
  onSelect,
  onDelete,
}: {
  field: FormField;
  /** 占几列（12 等分），由 layoutFieldGrid 算好 */
  colSpan: number;
  /** 网格行列定位（gridColumn/gridRow），贯穿格用 gridRow span 与后续行并排 */
  gridStyle: React.CSSProperties;
  /** 条目高度（px）：各自按 rows 显示，贯穿格是它盖住的几行行高之和 */
  heightPx: number;
  /** 仅分隔符条目：这一条边界是否真换页（连续两个及以上分隔符） */
  pageBreak?: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: field.id });

  const isDivider = field.type === 'divider';
  const isPureLabel = field.type === 'label';
  const topLabel = !isPureLabel && isTopLabel(field);
  const noLabel = !isPureLabel && isNoLabel(field);
  // 高条目（贯穿/多行留白）改纵向排布：把手/文字/删除各自水平居中；矮条目仍横排垂直居中
  const tallLabel = isPureLabel && heightPx > 64;

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
    width: '100%',
    height: heightPx,
    overflow: 'hidden',
    background: FIELD_TYPE_BG[field.type],
    ...gridStyle,
  };

  const handle = (
    <div className="field-drag-handle" {...attributes} {...listeners}>
      <HolderOutlined style={{ cursor: 'grab', color: '#999' }} />
    </div>
  );
  const label = (
    <span className={isPureLabel ? 'field-pure-label-text' : 'field-label-text'}>
      {isPureLabel ? splitLabelLines(field.label, colSpan / 12).join('\n') : field.label}
    </span>
  );
  const deleteBtn = (
    <Button
      type="text"
      size="small"
      danger
      className="field-del-btn"
      icon={<DeleteOutlined />}
      onClick={(e) => { e.stopPropagation(); onDelete(); }}
    />
  );
  // 按填写页真实版式摆：白色预览框里小字占位字符，超出省略号；下拉带箭头
  const previewBox = (
    <div className="field-body">
      <div className={`field-preview-box${field.type === 'textarea' ? ' multiline' : ''}`}>
        <span className="field-placeholder">{field.placeholder}</span>
        {field.type === 'select' && <CaretDownOutlined className="field-select-caret" />}
      </div>
    </div>
  );

  return (
    <Tooltip title={fieldDetailText(field)} mouseEnterDelay={0.4}>
      <div
        ref={setNodeRef}
        style={style}
        onClick={onSelect}
        className={`template-field-item ${isDivider ? 'divider' : ''} ${isSelected ? 'selected' : ''} ${topLabel ? 'label-top' : ''} ${tallLabel ? 'pure-label-tall' : ''}`}
      >
        {isDivider ? (
          <>{handle}<span className="field-divider-text"><BorderHorizontalOutlined />{pageBreak ? '分隔符 · 换页' : '分隔符'}</span>{deleteBtn}</>
        ) : isPureLabel ? (
          <>{handle}{label}{deleteBtn}</>
        ) : noLabel ? (
          <>{handle}{previewBox}{deleteBtn}</>
        ) : topLabel ? (
          <div className="field-top-row">{handle}{label}{deleteBtn}</div>
        ) : (
          <>{handle}{label}{previewBox}{deleteBtn}</>
        )}
        {!isPureLabel && topLabel && previewBox}
      </div>
    </Tooltip>
  );
}

/** 属性面板的一个设置块：标题行左侧名称、右侧当前值，控件在下 */
function PropBlock({ title, hint, children }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="prop-block">
      <div className="prop-label">
        <span>{title}</span>
        {hint && <span className="prop-hint">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export default function TemplateCreatePage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const templateId = searchParams.get('id');
  // Word 导入的确认态：字段来自 sessionStorage 草稿，保存才建档
  const fromImport = searchParams.get('fromImport') === '1';

  const [templateName, setTemplateName] = useState('');
  const [fields, setFields] = useState<FormField[]>([]);
  const [importSource, setImportSource] = useState('');
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!templateId);
  const [saving, setSaving] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  useEffect(() => { document.title = templateId ? '编辑模板' : '新增模板'; }, [templateId]);

  useEffect(() => {
    if (!templateId) {
      // 导入草稿：识别结果只存在 sessionStorage，确认保存前不建档，所以在这里读回来
      const draft = fromImport ? readImportDraft() : null;
      if (draft) {
        setTemplateName(draft.name);
        setImportSource(draft.sourceName);
        setFields(draft.fields);
        return;
      }
      setFields([]);
      return;
    }
    setLoading(true);
    templateApi.getById(Number(templateId))
      .then((t: TemplateItem) => {
        setTemplateName(t.name);
        setFields(t.content?.fields || []);
      })
      .catch(() => message.error('加载模板失败'))
      .finally(() => setLoading(false));
  }, [templateId, fromImport]);

  const selectedField = fields.find((f) => f.id === selectedFieldId) || null;

  const ROW_GAP = 6;
  // 条目自身高度：多行文本/纯标签按 rows 撑高，分隔符一条细横杠，其余 42px；标签在上的多加一排标签高度，预览框不被挤压
  const itemHeightPx = (f: FormField) => {
    if (f.type === 'divider') return 28;
    const base = f.type === 'textarea' || f.type === 'label' ? previewBoxHeight(f.rows, false) : 42;
    return f.type !== 'label' && isTopLabel(f) ? base + 20 : base;
  };
  // 画布按真实表格口径摆行：与填写页/详情/导出同一套 layoutFieldGrid（含 rowSpan 贯穿）。
  // 分隔符把画布切成几个各自独立的 12 列网格（一段就是一张表），分隔符条目排在其中两个网格之间
  const canvasBlocks = useMemo<CanvasBlock[]>(() => {
    const blocks: CanvasBlock[] = [];
    let current: FormField[] = [];
    let dividers: FormField[] = [];
    const flushGrid = () => {
      if (current.length === 0) return;
      const rows = layoutFieldGrid(current);
      blocks.push({
        kind: 'grid',
        key: rows[0]?.cells[0]?.field.id ?? `grid-${blocks.length}`,
        rows,
        // 行高 = 本行起始的非贯穿条目里最高者：只用来给贯穿格算它盖住的几行总高
        rowHeights: rows.map((row) =>
          Math.max(42, ...row.cells.filter((c) => c.rowSpan === 1).map((c) => itemHeightPx(c.field))),
        ),
      });
      current = [];
    };
    const flushDividers = () => {
      // 连续 ≥2 个分隔符才是换页边界，单独一个只断开表格
      dividers.forEach((field) => blocks.push({ kind: 'divider', field, pageBreak: dividers.length >= 2 }));
      dividers = [];
    };
    for (const field of fields) {
      if (field.type === 'divider') {
        flushGrid();
        dividers.push(field);
        continue;
      }
      flushDividers();
      current.push(field);
    }
    flushGrid();
    flushDividers();
    return blocks;
  }, [fields]);

  const addField = useCallback((type: FormField['type']) => {
    const newField: FormField = {
      id: `field-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      type,
      label: FIELD_TYPE_MAP[type],
      placeholder: type === 'label' || type === 'divider' ? undefined : '',
      options: type === 'select' ? ['选项1', '选项2'] : undefined,
    };
    setFields((prev) => [...prev, newField]);
    setSelectedFieldId(newField.id);
  }, []);

  const deleteField = useCallback((id: string) => {
    setFields((prev) => prev.filter((f) => f.id !== id));
    if (selectedFieldId === id) setSelectedFieldId(null);
  }, [selectedFieldId]);

  const updateField = useCallback((id: string, updates: Partial<FormField>) => {
    setFields((prev) => prev.map((f) => f.id === id ? { ...f, ...updates } : f));
  }, []);

  const changeFieldType = useCallback((field: FormField, type: FormField['type']) => {
    if (field.type === type) return;
    const updates: Partial<FormField> = { type };
    if (type === 'select' && !field.options?.length) updates.options = ['选项1', '选项2'];
    if (type !== 'select') updates.options = undefined;
    if (type === 'label') updates.placeholder = undefined;
    // 分隔符没有栏目名、也没有版式可设：把无关属性清掉，省得排版读到残留值
    if (type === 'divider') {
      updates.label = FIELD_TYPE_MAP.divider;
      updates.placeholder = undefined;
      updates.labelPosition = undefined;
      updates.width = undefined;
      updates.rows = undefined;
      updates.rowSpan = undefined;
    }
    updateField(field.id, updates);
  }, [updateField]);

  const handleDragStart = (event: DragStartEvent) => {
    setActiveId(event.active.id as string);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveId(null);
    if (!over) return;
    if (active.id !== over.id) {
      setFields((prev) => {
        const oldIndex = prev.findIndex((f) => f.id === active.id);
        const newIndex = prev.findIndex((f) => f.id === over.id);
        if (oldIndex === -1 || newIndex === -1) return prev;
        return arrayMove(prev, oldIndex, newIndex);
      });
    }
  };

  const handleCanvasDrop = (e: React.DragEvent) => {
    e.preventDefault();
    dragCounter = 0;
    const type = e.dataTransfer.getData('field-type') as FormField['type'];
    if (type && FIELD_TYPES.some((ft) => ft.type === type)) {
      addField(type);
    }
  };

  const handleCanvasDragOver = (e: React.DragEvent) => { e.preventDefault(); };
  const handleCanvasDragEnter = (e: React.DragEvent) => { e.preventDefault(); dragCounter++; };
  const handleCanvasDragLeave = () => { dragCounter--; };

  const handleSave = async () => {
    if (!templateName.trim()) { message.warning('请输入模板名称'); return; }
    setSaving(true);
    try {
      const schema: FormSchema = { fields };
      if (templateId) {
        await templateApi.update(Number(templateId), { name: templateName, content: schema });
      } else {
        await templateApi.create({ name: templateName, content: schema });
      }
      message.success('保存成功');
      // 草稿只服务这一次确认，建档后原文件与草稿都不必留存
      if (fromImport) sessionStorage.removeItem(TEMPLATE_IMPORT_DRAFT_KEY);
      router.push(ROUTES.TEMPLATE_MANAGEMENT);
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

  return (
    <div className="template-editor-root">
      <style>{`
        .template-editor-layout { display: flex; gap: 16px; }
        .template-editor-sidebar { width: 200px; flex-shrink: 0; }
        /* min-width:0：画布是 flex:1 弹性项，默认按内容最小宽度撑开，窄条目里的长标签会把整栏顶出视口（横向滚动条） */
        .template-editor-canvas { flex: 1; min-width: 0; min-height: 400px; }
        .template-editor-props { width: 280px; flex-shrink: 0; }
        /* 桌面端与填写页同一口径：根节点撑满 Content（禁 100vh calc，扣多少凭猜会漏出页面滚动条），
           三栏吃剩余高度、卡片内部滚动，页面本身不出滚动条；窄屏纵向堆叠时退回页面滚动 */
        @media (min-width: 769px) {
          .template-editor-root { height: 100%; display: flex; flex-direction: column; }
          .template-editor-root > *:not(.template-editor-layout) { flex-shrink: 0; }
          .template-editor-layout { flex: 1; min-height: 0; }
          .template-editor-sidebar, .template-editor-canvas, .template-editor-props { height: 100%; min-height: 0; }
          .template-editor-layout .ant-card { height: 100%; display: flex; flex-direction: column; }
          .template-editor-layout .ant-card-body { flex: 1; min-height: 0; overflow: auto; }
        }
        .palette-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; margin-bottom: 6px; border: 1px solid #d9d9d9; border-radius: 6px; cursor: grab; transition: all 0.2s; font-size: 13px; background: #fff; user-select: none; }
        .palette-item:hover { border-color: #1677ff; color: #1677ff; box-shadow: 0 2px 4px rgba(22,119,255,0.1); }
        /* 类型底色与画布条目同一套：面板条目整条上色，切换器图标着同色系色 */
        .prop-block { margin-bottom: 16px; }
        .prop-label { display: flex; align-items: baseline; justify-content: space-between; font-size: 12px; color: rgba(0,0,0,0.45); margin-bottom: 6px; }
        .prop-hint { color: rgba(0,0,0,0.75); }
        /* 滑杆左右留白：两端刻度（1 / 全宽）文字不再被面板裁边 */
        .prop-slider { margin: 4px 10px 22px; }
        .type-seg-label { display: inline-flex; align-items: center; justify-content: center; min-width: 26px; padding: 1px 6px; border-radius: 4px; font-size: 14px; }
        .canvas-drop-zone { min-height: 350px; border: 2px dashed #d9d9d9; border-radius: 8px; padding: 12px; transition: all 0.2s; }
        .canvas-drop-zone.drag-over { border-color: #1677ff; background: #f0f5ff; }
        .template-field-item { display: flex; align-items: stretch; gap: 6px; padding: 6px 8px; border: 1px solid #d9d9d9; border-radius: 6px; cursor: pointer; transition: all 0.15s; box-sizing: border-box; }
        .template-field-item:hover { border-color: #91caff; }
        .template-field-item.selected { border-color: #1677ff; box-shadow: 0 0 0 2px rgba(22,119,255,0.1); }
        /* 标签在上：条目纵向两排，与填写页 vertical 布局同形；标签在左就是同行左列，预览框撑满剩余空间 */
        .template-field-item.label-top { flex-direction: column; }
        .field-top-row { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
        .field-drag-handle { flex-shrink: 0; display: flex; align-items: center; align-self: center; }
        .field-label-text { align-self: center; flex: 0 1 auto; min-width: 0; max-width: 45%; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-weight: 600; font-size: 12px; line-height: 18px; color: rgba(0,0,0,0.88); }
        .field-pure-label-text { flex: 1; min-width: 0; display: flex; align-items: center; justify-content: center; text-align: center; font-weight: 600; font-size: 13px; line-height: 22px; white-space: pre-line; color: rgba(0,0,0,0.88); }
        /* 竖排纯标签很高：条目改纵向排布，把手顶部居中、文字居中、删除底部居中，不缩在角落 */
        .template-field-item.pure-label-tall { flex-direction: column; align-items: center; }
        .template-field-item.pure-label-tall .field-drag-handle { flex-shrink: 0; }
        .template-field-item.pure-label-tall .field-del-btn { flex-shrink: 0; }
        .field-del-btn { align-self: center; flex-shrink: 0; }
        .field-body { flex: 1 1 auto; min-width: 0; display: flex; }
        .field-preview-box { flex: 1; min-width: 0; display: flex; align-items: center; gap: 4px; background: #fff; border: 1px solid #d9d9d9; border-radius: 4px; padding: 0 7px; box-sizing: border-box; }
        .field-preview-box.multiline { align-items: flex-start; padding: 3px 7px; }
        .field-placeholder { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 12px; line-height: 20px; color: #a6a6a6; }
        .field-select-caret { flex-shrink: 0; font-size: 10px; color: #bfbfbf; }
        /* 分隔符画成整行虚线条：它不在表格里，只是把上下两部分各成一张表 */
        .template-field-item.divider { border-style: dashed; border-color: #bfbfbf; margin: 6px 0; }
        .field-divider-text { flex: 1; min-width: 0; display: flex; align-items: center; justify-content: center; gap: 6px; font-size: 12px; color: rgba(0,0,0,0.45); }
        @media (max-width: 768px) { .template-editor-layout { flex-direction: column; } .template-editor-sidebar, .template-editor-props { width: 100%; } }
      `}</style>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => router.push(ROUTES.TEMPLATE_MANAGEMENT)}>返回</Button>
          <Title level={4} style={{ margin: 0 }}>{templateId ? '编辑模板' : '新增模板'}</Title>
        </Space>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>保存</Button>
      </div>

      <div style={{ marginBottom: 16 }}>
        <Text strong style={{ marginRight: 8 }}>模板名称：</Text>
        <Input value={templateName} onChange={(e) => setTemplateName(e.target.value)} placeholder="请输入模板名称" style={{ maxWidth: 400 }} />
      </div>

      {fromImport && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={`这些字段由 Word 文档识别而来${importSource ? `（${importSource}）` : ''}`}
          description="还没有保存：可以先调整标签、宽度、标签位置和高度，点右上角「保存」后才会建档。"
        />
      )}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
        <div className="template-editor-layout">
          {/* Left: Field Palette */}
          <div className="template-editor-sidebar">
            <Card title="字段面板" size="small">
              {FIELD_TYPES.map((ft) => (
                <div key={ft.type} className="palette-item" style={{ background: FIELD_TYPE_BG[ft.type] }} draggable onDragStart={(e) => { e.dataTransfer.setData('field-type', ft.type); e.dataTransfer.effectAllowed = 'copy'; }} onClick={() => addField(ft.type)}>
                  <span className="palette-icon" style={{ color: FIELD_TYPE_COLOR[ft.type] }}>{ft.icon}</span>
                  <span>{ft.label}</span>
                </div>
              ))}
            </Card>
          </div>

          {/* Center: Canvas */}
          <div className="template-editor-canvas">
            <Card title="表单画布" size="small" styles={{ body: { padding: 12 } }}>
              <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
                字段按宽度自动分行（1/12 递增共十二档）；「纯标签」只放栏目标题、不需要填写内容，适合标题套标题的分组行，宽度 1/12 时一字一行（竖排标题）；竖排大标题可设「跨行数」，它纵向贯穿下面几行、行首留给它的列宽；「分隔符」没有内容，只把它上下的字段各成一张独立的表，紧挨着放两个则再换一页；所有字段一视同仁，都可删除、改标签、切类型
              </Text>
              <div className="canvas-drop-zone" onDrop={handleCanvasDrop} onDragOver={handleCanvasDragOver} onDragEnter={handleCanvasDragEnter} onDragLeave={handleCanvasDragLeave} id="canvas-drop-area">
                {fields.length === 0 ? (
                  <Empty description="从左侧拖入或点击字段类型添加" style={{ margin: '40px 0' }} />
                ) : (
                  <SortableContext items={fields.map((f) => f.id)} strategy={verticalListSortingStrategy}>
                    {canvasBlocks.map((block) =>
                      block.kind === 'divider' ? (
                        <SortableField
                          key={block.field.id}
                          field={block.field}
                          colSpan={12}
                          gridStyle={{}}
                          heightPx={itemHeightPx(block.field)}
                          pageBreak={block.pageBreak}
                          isSelected={selectedFieldId === block.field.id}
                          onSelect={() => setSelectedFieldId(block.field.id)}
                          onDelete={() => deleteField(block.field.id)}
                        />
                      ) : (
                        /* 分隔符每隔开一段就是一张独立的 12 列网格（与填写页/详情/导出同一口径）：
                           贯穿格用 gridRow span 与它盖住的行并排，而不是把后面的行顶下去 */
                        <div
                          key={block.key}
                          style={{
                            display: 'grid',
                            gridTemplateColumns: 'repeat(12, 1fr)',
                            gridTemplateRows: block.rowHeights.map((h) => `${h}px`).join(' '),
                            gap: `${ROW_GAP}px 8px`,
                          }}
                        >
                          {block.rows.flatMap((row, ri) => {
                            let col = row.covered;
                            return row.cells.map((cell) => {
                              const gridStyle: React.CSSProperties = {
                                gridColumn: `${col + 1} / span ${cell.colSpan}`,
                                gridRow: `${ri + 1} / span ${cell.rowSpan}`,
                              };
                              col += cell.colSpan;
                              return (
                                <SortableField
                                  key={cell.field.id}
                                  field={cell.field}
                                  colSpan={cell.colSpan}
                                  gridStyle={gridStyle}
                                  heightPx={cell.rowSpan > 1
                                    ? block.rowHeights.slice(ri, ri + cell.rowSpan).reduce((s, h) => s + h, 0) + ROW_GAP * (cell.rowSpan - 1)
                                    : itemHeightPx(cell.field)}
                                  isSelected={selectedFieldId === cell.field.id}
                                  onSelect={() => setSelectedFieldId(cell.field.id)}
                                  onDelete={() => deleteField(cell.field.id)}
                                />
                              );
                            });
                          })}
                        </div>
                      ),
                    )}
                  </SortableContext>
                )}
              </div>
            </Card>
          </div>

          {/* Right: Property Editor */}
          <div className="template-editor-props">
            <Card title="字段属性" size="small">
              {selectedField ? (
                <div key={selectedField.id}>
                  <PropBlock title="字段类型">
                    <Segmented
                      size="small"
                      block
                      options={FIELD_TYPES.map((ft) => ({
                        label: (
                          <span
                            className="type-seg-label"
                            style={{ background: FIELD_TYPE_BG[ft.type], color: FIELD_TYPE_COLOR[ft.type] }}
                            title={FIELD_TYPE_MAP[ft.type]}
                          >
                            {ft.icon}
                          </span>
                        ),
                        value: ft.type,
                      }))}
                      value={selectedField.type}
                      onChange={(v) => changeFieldType(selectedField, v as FormField['type'])}
                    />
                  </PropBlock>
                  {/* 分隔符只是一条断点，除类型外没有任何可设置的属性 */}
                  {selectedField.type === 'divider' && (
                    <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                      分隔符没有可设置的属性：它把上下的字段各成一张独立的表，紧挨着放两个再换一页。
                    </Text>
                  )}
                  {selectedField.type !== 'divider' && (
                    <PropBlock title="标签名称">
                      <Input size="small" value={selectedField.label} onChange={(e) => updateField(selectedField.id, { label: e.target.value })} />
                    </PropBlock>
                  )}
                  {/* 纯标签只有标题，占位文本/标签位置/候选项都没有意义 */}
                  {selectedField.type !== 'label' && selectedField.type !== 'divider' && (
                    <PropBlock title="占位文本">
                      <Input size="small" value={selectedField.placeholder || ''} onChange={(e) => updateField(selectedField.id, { placeholder: e.target.value })} />
                    </PropBlock>
                  )}
                  {selectedField.type !== 'label' && selectedField.type !== 'divider' && (
                    <PropBlock title="标签位置">
                      <Segmented
                        size="small"
                        block
                        options={[{ label: '左侧', value: 'left' }, { label: '顶部', value: 'top' }, { label: '无', value: 'none' }]}
                        value={selectedField.labelPosition || 'left'}
                        onChange={(v) => updateField(selectedField.id, { labelPosition: v as 'left' | 'top' | 'none' })}
                      />
                    </PropBlock>
                  )}
                  {/* 高度（行）：多行文本定展示框高度；不贯穿的纯标题定留白高度。贯穿标题的高度由它盖住的行决定，不再单独给 */}
                  {(selectedField.type === 'textarea' || (selectedField.type === 'label' && (selectedField.rowSpan ?? 1) <= 1)) && (
                    <PropBlock title="高度（行）" hint={`${selectedField.rows ?? 4} 行`}>
                      <Slider
                        min={2}
                        max={20}
                        step={1}
                        marks={{ 2: '2', 4: '4', 8: '8', 12: '12', 16: '16', 20: '20' }}
                        value={selectedField.rows ?? 4}
                        onChange={(v) => updateField(selectedField.id, { rows: v as number })}
                        tooltip={{ formatter: (v) => `${v} 行` }}
                        className="prop-slider"
                      />
                    </PropBlock>
                  )}
                  {selectedField.type !== 'divider' && (
                    <PropBlock title="宽度" hint={(() => { const n = Math.round(fieldWidth(selectedField) * 12); return n === 12 ? '全宽' : twelfthsLabel(n); })()}>
                      <Slider
                        min={1}
                        max={12}
                        step={1}
                        marks={{ 3: '1/4', 6: '1/2', 9: '3/4', 12: '全宽' }}
                        value={Math.round(fieldWidth(selectedField) * 12)}
                        onChange={(v) => updateField(selectedField.id, { width: (v as number) / 12 })}
                        tooltip={{ formatter: (v) => (v === 12 ? '全宽' : twelfthsLabel(v as number)) }}
                        className="prop-slider"
                      />
                    </PropBlock>
                  )}
                  {/* 跨行数：竖排大标题纵向贯穿下面几行（Word 表格里的合并格），1 = 不贯穿。当前值放标题右侧，刻度只留数字 */}
                  {selectedField.type === 'label' && (
                    <PropBlock title="跨行数" hint={(selectedField.rowSpan ?? 1) <= 1 ? '不贯穿' : `贯穿 ${selectedField.rowSpan} 行`}>
                      <Slider
                        min={1}
                        max={12}
                        step={1}
                        marks={{ 1: '1', 2: '2', 4: '4', 6: '6', 9: '9', 12: '12' }}
                        value={selectedField.rowSpan ?? 1}
                        onChange={(v) => updateField(selectedField.id, { rowSpan: v === 1 ? undefined : (v as number) })}
                        tooltip={{ formatter: (v) => (v === 1 ? '不贯穿' : `贯穿 ${v} 行`) }}
                        className="prop-slider"
                      />
                    </PropBlock>
                  )}
                  {selectedField.type === 'select' && (
                    <PropBlock title="选项（每行一个）">
                      <Input.TextArea size="small" rows={4} value={(selectedField.options || []).join('\n')} onChange={(e) => updateField(selectedField.id, { options: e.target.value.split('\n').filter((s) => s.trim()) })} />
                    </PropBlock>
                  )}

                </div>
              ) : (
                <Empty description="点击字段进行编辑" style={{ margin: '40px 0' }} />
              )}
            </Card>
          </div>
        </div>

        <DragOverlay>
          {activeId ? (
            <div className="palette-item" style={{ opacity: 0.9, boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}>
              {FIELD_TYPES.find((ft) => ft.type === activeId)?.icon}
              <span>{FIELD_TYPES.find((ft) => ft.type === activeId)?.label}</span>
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
    </div>
  );
}
