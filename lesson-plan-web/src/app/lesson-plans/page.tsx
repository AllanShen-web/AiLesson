'use client';

import { useEffect, useState, useMemo, useCallback, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import {
  Button, Input, Space, Table, Tag, Typography, Select, Tabs, Tree,
  Modal, Popconfirm, message, Tooltip, Dropdown, Popover,
} from 'antd';
import {
  PlusOutlined, CopyOutlined, DeleteOutlined,
  EditOutlined, SwapOutlined, EyeOutlined, UnorderedListOutlined,
  ApartmentOutlined, SortAscendingOutlined, SortDescendingOutlined,
  FileOutlined, MoreOutlined,
} from '@ant-design/icons';
import { useRouter } from 'next/navigation';
import { ROUTES } from '@/constants/routes';
import { useMobile } from '@/hooks/useMobile';
import { useAuth } from '@/contexts/AuthContext';
import { formAgentStore } from '@/lib/formAgentStore';
import { chatUiStore } from '@/lib/chatUiStore';
import GradeSelect from '@/components/GradeSelect';
import { RichTextPreview } from '@/components/RichTextEditor';
import {
  lessonPlanApi, templateApi, layoutFieldGrid, splitByDividers, isTopLabel, isNoLabel, previewBoxHeight, splitLabelLines,
  type LessonPlanItem, type LessonPlanQuery, type TemplateItem,
} from '@/lib/api';

const { Title } = Typography;

type FileKind = 'ppt' | 'word' | 'pdf';

const FAN_W = 76;
const FAN_H = 106;

// 右侧张开的扇形：面板左边中点就是「生成文件」按钮的位置，三枚圆圈沿圆弧向右排开（圆心距 38，圆圈 32，不会相碰），
// tilt 让上下两枚向外倾，读起来像张开的折扇。left/top 单位 px
const FILE_FAN = [
  { key: 'ppt', text: 'PPT', left: 27, top: 1, tilt: -12 },
  { key: 'word', text: 'DOC', left: 40, top: 37, tilt: 0 },
  { key: 'pdf', text: 'PDF', left: 27, top: 73, tilt: 12 },
] as const;

function FileFan({ onPick }: { onPick: (kind: FileKind) => void }) {
  return (
    <Popover
      trigger={['hover']}
      placement="right"
      arrow={false}
      styles={{ container: { background: 'transparent', boxShadow: 'none', padding: 0 } }}
      content={
        <div style={{ position: 'relative', width: FAN_W, height: FAN_H }}>
          {FILE_FAN.map((item) => (
            <Button
              key={item.key}
              shape="circle"
              color="primary"
              variant="outlined"
              style={{
                position: 'absolute',
                left: item.left,
                top: item.top,
                padding: 0,
                fontSize: 10,
                fontWeight: 700,
                transform: `rotate(${item.tilt}deg)`,
                boxShadow: '0 2px 8px rgba(0, 0, 0, 0.16)',
              }}
              onClick={() => onPick(item.key)}
            >
              {item.text}
            </Button>
          ))}
        </div>
      }
    >
      <Button size="small" color="primary" variant="text" icon={<FileOutlined />} />
    </Popover>
  );
}

const MARKDOWN_STYLES = `
  .lp-markdown { font-size: 14px; line-height: 1.75; color: #333; word-break: break-word; }
  .lp-markdown h1, .lp-markdown h2, .lp-markdown h3, .lp-markdown h4 { margin: 14px 0 8px; font-weight: 600; line-height: 1.4; }
  .lp-markdown h1 { font-size: 20px; } .lp-markdown h2 { font-size: 18px; }
  .lp-markdown h3 { font-size: 16px; } .lp-markdown h4 { font-size: 14px; }
  .lp-markdown p { margin: 6px 0; }
  .lp-markdown ul, .lp-markdown ol { padding-left: 22px; margin: 6px 0; }
  .lp-markdown li { margin: 2px 0; }
  .lp-markdown code { background: #f2f2f2; padding: 1px 5px; border-radius: 4px; font-size: 13px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  .lp-markdown pre { background: #f6f8fa; border: 1px solid #e5e7eb; padding: 12px 14px; border-radius: 8px; overflow-x: auto; margin: 8px 0; }
  .lp-markdown pre code { background: transparent; padding: 0; color: inherit; }
  .lp-markdown table { border-collapse: collapse; margin: 8px 0; width: 100%; display: block; overflow-x: auto; }
  .lp-markdown th, .lp-markdown td { border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left; }
  .lp-markdown th { background: #f9fafb; font-weight: 600; }
  .lp-markdown blockquote { border-left: 3px solid #667eea; padding: 2px 12px; margin: 8px 0; color: #666; background: #f8f8fc; border-radius: 0 6px 6px 0; }
  .lp-markdown a { color: #667eea; }
  .lp-markdown img { max-width: 100%; border-radius: 6px; }
  .lp-markdown hr { border: none; border-top: 1px solid #e5e7eb; margin: 12px 0; }
  .lp-markdown .hljs-comment, .lp-markdown .hljs-quote { color: #6a737d; font-style: italic; }
  .lp-markdown .hljs-keyword, .lp-markdown .hljs-selector-tag { color: #d73a49; }
  .lp-markdown .hljs-string, .lp-markdown .hljs-attr { color: #032f62; }
  .lp-markdown .hljs-number, .lp-markdown .hljs-literal { color: #005cc5; }
  .lp-markdown .hljs-title, .lp-markdown .hljs-function .hljs-title { color: #6f42c1; }
  .lp-markdown .hljs-built_in, .lp-markdown .hljs-type { color: #e36209; }
`;

function MarkdownView({ content }: { content: string }) {
  return (
    <div className="lp-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {content}
      </ReactMarkdown>
      <style>{MARKDOWN_STYLES}</style>
    </div>
  );
}

/** 字段正文：富文本字段按 HTML 渲染（与编辑页一致），其余仍按 Markdown */
function FieldValue({ field, val }: { field: any; val: string }) {
  return field.type === 'textarea'
    ? <RichTextPreview value={val} bare rows={field.rows} />
    : <MarkdownView content={val} />;
}

export default function LessonPlansPage() {
  const isMobile = useMobile();
  const { user } = useAuth();
  const router = useRouter();
  useEffect(() => { document.title = '教案管理'; }, []);

  const [activeTab, setActiveTab] = useState<'personal' | 'public'>('personal');
  const [viewMode, setViewMode] = useState<'flat' | 'tree'>('flat');
  const [data, setData] = useState<LessonPlanItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [allData, setAllData] = useState<LessonPlanItem[]>([]);
  const [allLoading, setAllLoading] = useState(false);

  // 筛选状态
  const [subject, setSubject] = useState('');
  const [grade, setGrade] = useState('');
  const [keyword, setKeyword] = useState('');
  const [sortField, setSortField] = useState<'createdAt' | 'updatedAt'>('createdAt');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 20;

  // 详情弹窗
  const [detailItem, setDetailItem] = useState<LessonPlanItem | null>(null);

  // 模板选择弹窗
  const [templateModalOpen, setTemplateModalOpen] = useState(false);
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      templateApi.getMy({ signal: controller.signal }),
      templateApi.getPublic({ signal: controller.signal }),
    ])
      .then(([my, pub]) => {
        const merged = [...my];
        for (const t of pub) {
          if (!merged.find((m) => m.id === t.id)) merged.push(t);
        }
        if (!controller.signal.aborted) setTemplates(merged);
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);

  // 单一下拉：我的模板排前，其余为公开模板；每项用标签标明私有/公开
  const templateChoices = [...templates]
    .sort((a, b) => Number(b.userId === user?.id) - Number(a.userId === user?.id))
    .map((t) => ({
      label: `${t.name}${t.isOfficial ? '（官方）' : ''}`,
      value: t.id,
      scope: t.isPublic ? '公开' : '私有',
    }));

  const buildQuery = useCallback((): LessonPlanQuery => {
    return { subject, grade, keyword, sortField, sortOrder, page, pageSize: PAGE_SIZE };
  }, [subject, grade, keyword, sortField, sortOrder, page]);

  const buildAllQuery = useCallback(() => {
    return { subject, grade, keyword };
  }, [subject, grade, keyword]);

  const loadFlat = useCallback(async (signal?: AbortSignal) => {
    setData([]);
    setLoading(true);
    try {
      const query = buildQuery();
      const result = activeTab === 'personal'
        ? await lessonPlanApi.getMy(query, { signal })
        : await lessonPlanApi.getPublic(query, { signal });
      if (!signal?.aborted) {
        setData(result.items);
        setTotal(result.total);
      }
    } catch {
      if (!signal?.aborted) message.error('加载失败');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [activeTab, buildQuery]);

  const loadAll = useCallback(async (signal?: AbortSignal) => {
    setAllData([]);
    setAllLoading(true);
    try {
      const query = buildAllQuery();
      const result = activeTab === 'personal'
        ? await lessonPlanApi.getMyAll(query, { signal })
        : await lessonPlanApi.getPublicAll(query, { signal });
      if (!signal?.aborted) setAllData(result);
    } catch {
      if (!signal?.aborted) message.error('加载失败');
    } finally {
      if (!signal?.aborted) setAllLoading(false);
    }
  }, [activeTab, buildAllQuery]);

  useEffect(() => {
    const controller = new AbortController();
    if (viewMode === 'flat') loadFlat(controller.signal);
    else loadAll(controller.signal);
    return () => controller.abort();
  }, [viewMode, loadFlat, loadAll]);

  // 筛选变化时重置页码
  useEffect(() => { setPage(1); }, [subject, grade, keyword, sortField, sortOrder, activeTab]);

  const openDetail = useCallback((record: LessonPlanItem) => {
    setDetailItem(record);
    // 详情里的 AI 助手要针对这篇教案，否则会继续用上一次选中的 id
    formAgentStore.setLessonPlanId(record.id);
  }, []);

  // 树状数据分组
  const treeData = useMemo(() => {
    const map = new Map<string, Map<string, LessonPlanItem[]>>();
    for (const item of allData) {
      if (!map.has(item.subject)) map.set(item.subject, new Map());
      const gradeMap = map.get(item.subject)!;
      if (!gradeMap.has(item.grade)) gradeMap.set(item.grade, []);
      gradeMap.get(item.grade)!.push(item);
    }
    return Array.from(map.entries()).map(([subj, gradeMap]) => ({
      title: subj,
      key: `subject-${subj}`,
      children: Array.from(gradeMap.entries()).map(([g, items]) => ({
        title: g,
        key: `grade-${subj}-${g}`,
        children: items.map((item) => ({
          title: (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              <span>{item.title}</span>
              <Tag color={item.isPublic ? 'green' : 'default'} style={{ marginLeft: 4 }}>
                {item.isPublic ? '公开' : '私有'}
              </Tag>
              <Tooltip title="查看详情">
                <EyeOutlined
                  style={{ color: '#1677ff', cursor: 'pointer' }}
                  onClick={(e) => { e.stopPropagation(); openDetail(item); }}
                />
              </Tooltip>
            </span>
          ),
          key: `plan-${item.id}`,
          item,
        })),
      })),
    }));
  }, [allData, openDetail]);

  const handleCopy = async (id: number) => {
    try {
      await lessonPlanApi.copy(id);
      message.success('复制成功，已生成私有副本');
      if (viewMode === 'flat') loadFlat();
      else loadAll();
    } catch {
      message.error('复制失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await lessonPlanApi.remove(id);
      message.success('删除成功');
      if (viewMode === 'flat') loadFlat();
      else loadAll();
    } catch {
      message.error('删除失败');
    }
  };

  const handleTogglePublic = async (item: LessonPlanItem) => {
    try {
      await lessonPlanApi.togglePublic(item.id);
      message.success(item.isPublic ? '已设为私有' : '已设为公开');
      if (viewMode === 'flat') loadFlat();
      else loadAll();
    } catch {
      message.error('操作失败');
    }
  };

  // 三种格式都交给 AI 助手：它清楚每种排版的生成链路，也会把文件挂到会话里供下载
  const handleGenerate = (record: LessonPlanItem, kind: FileKind) => {
    const action = kind === 'word' ? '导出 Word 文档' : kind === 'pdf' ? '导出 PDF' : '生成 PPT';
    chatUiStore.requestSend(`把教案《${record.title}》${action}`, record.id);
  };

  // 六列全部定宽：固定布局下，容器比列宽总和大时，富余空间按各列宽度比例分摊。
  // 只要有一列不写 width，它就会独吞全部富余空间——宽屏下标题列被拉到夸张的长度。
  const columns = [
    {
      title: '教案标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (v: string, record: LessonPlanItem) => (
        <a onClick={() => openDetail(record)} style={{ fontWeight: 500 }}>{v}</a>
      ),
    },
    {
      title: '科目',
      dataIndex: 'subject',
      key: 'subject',
      width: 120,
      render: (v: string, record: LessonPlanItem) => v || String(record.content?.values?.['basic-subject'] ?? '') || '-',
    },
    {
      title: '年级',
      dataIndex: 'grade',
      key: 'grade',
      //width: 140,
      render: (v: string, record: LessonPlanItem) => v || String(record.content?.values?.['basic-grade'] ?? '') || '-',
    },
    {
      title: '公开状态',
      dataIndex: 'isPublic',
      key: 'isPublic',
      width: 120,
      render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '公开' : '私有'}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      //width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      // 编辑 / 生成文件 / 删除 / 更多，四枚纯图标按钮
      //width: 140,
      fixed: 'right' as const,
      render: (_: unknown, record: LessonPlanItem) => {
        // 详情已经由标题点击承担，这里只剩非作者能做的复制
        if (user?.id !== record.userId) {
          return (
            <Popconfirm title="确认复制？" onConfirm={() => handleCopy(record.id)} okText="复制" cancelText="取消">
              <Tooltip title="复制">
                <Button size="small" color="primary" variant="text" icon={<CopyOutlined />} />
              </Tooltip>
            </Popconfirm>
          );
        }
        return (
          <Space size="small">
            <Tooltip title="编辑">
              <Button
                size="small"
                color="primary"
                variant="text"
                icon={<EditOutlined />}
                onClick={() => router.push(`${ROUTES.LESSON_PLAN_CREATE}?id=${record.id}`)}
              />
            </Tooltip>
            <FileFan onPick={(kind) => handleGenerate(record, kind)} />
            <Popconfirm title="确认删除？" onConfirm={() => handleDelete(record.id)} okText="删除" cancelText="取消">
              <Tooltip title="删除">
                <Button size="small" color="danger" variant="text" icon={<DeleteOutlined />} />
              </Tooltip>
            </Popconfirm>
            <Dropdown
              trigger={['hover']}
              menu={{
                items: [
                  { key: 'copy', icon: <CopyOutlined />, label: '复制' },
                  { key: 'public', icon: <SwapOutlined />, label: record.isPublic ? '私有' : '公开' },
                ],
                onClick: ({ key }) => (key === 'copy' ? handleCopy(record.id) : handleTogglePublic(record)),
              }}
            >
              <Button size="small" color="primary" variant="text" icon={<MoreOutlined />} />
            </Dropdown>
          </Space>
        );
      },
    },
  ];

  const sortIcon = sortOrder === 'desc' ? <SortDescendingOutlined /> : <SortAscendingOutlined />;

  return (
    <div>
      {/* 标题栏 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>教案管理</Title>
        <Space>
          <Tooltip title={viewMode === 'flat' ? '切换到树状视图' : '切换到列表视图'}>
            <Button
              icon={viewMode === 'flat' ? <ApartmentOutlined /> : <UnorderedListOutlined />}
              onClick={() => setViewMode(viewMode === 'flat' ? 'tree' : 'flat')}
            />
          </Tooltip>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setSelectedTemplateId(null); setTemplateModalOpen(true); }}
            style={{ visibility: activeTab === 'personal' ? 'visible' : 'hidden' }}
          >
            新建教案
          </Button>
        </Space>
      </div>

      {/* 筛选工具栏 */}
      <Space wrap style={{ marginBottom: 16 }} size="middle">
        <Input
          placeholder="科目"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          style={{ width: 140 }}
          allowClear
        />
        <GradeSelect
          value={grade || undefined}
          onChange={(v) => setGrade((v as string) || '')}
          style={{ width: 180 }}
          allowClear
          remember={false}
        />
        <Input.Search
          placeholder="搜索标题/内容"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={(v) => setKeyword(v)}
          style={{ width: 200 }}
          allowClear
        />
        <Tooltip title="切换排序字段">
          <Button icon={sortIcon} onClick={() => setSortOrder(sortOrder === 'desc' ? 'asc' : 'desc')}>
            {sortField === 'createdAt' ? '创建时间' : '更新时间'}
          </Button>
        </Tooltip>
        <Select
          value={sortField}
          onChange={(v) => setSortField(v)}
          style={{ width: 110 }}
          options={[
            { label: '创建时间', value: 'createdAt' },
            { label: '更新时间', value: 'updatedAt' },
          ]}
        />
      </Space>

      {/* Tab 切换 */}
      <Tabs
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as 'personal' | 'public')}
        items={[
          { key: 'personal', label: '我的教案' },
          { key: 'public', label: '公共教案' },
        ]}
      />

      {/* 扁平列表视图 */}
      {viewMode === 'flat' && (
        <Table
          columns={columns}
          dataSource={data}
          rowKey="id"
          loading={loading}
          size={isMobile ? 'small' : 'middle'}
          tableLayout="fixed"
          scroll={{ x: 990 }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            showSizeChanger: false,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p) => setPage(p),
          }}
        />
      )}

      {/* 树状列表视图 */}
      {viewMode === 'tree' && (
        <div style={{ overflowX: 'auto' }}>
          {allLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>加载中...</div>
          ) : treeData.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无数据</div>
          ) : (
            <Tree defaultExpandAll treeData={treeData} style={{ fontSize: 14 }} />
          )}
        </div>
      )}

      {/* 教案详情弹窗 */}
      <Modal
        open={!!detailItem}
        onCancel={() => { setDetailItem(null); formAgentStore.setLessonPlanId(null); }}
        footer={null}
        width={isMobile ? '95%' : 760}
        styles={{ body: { padding: 0, borderRadius: 12 } }}
        destroyOnHidden
      >
        {detailItem && (
          <div style={{ overflow: 'hidden', borderRadius: 12 }}>
            {/* 字段内容：按模板宽度分行，与填写页、导出文件同一套分组口径 */}
            <div style={{ padding: '0 24px 22px', maxHeight: '60vh', overflowY: 'auto' }}>
              {(() => {
                const fields = detailItem.content?.fields || [];

                const FieldCard = ({ field, colSpan, rowSpan }: { field: any; colSpan: number; rowSpan: number }) => {
                  const val = detailItem.content?.values?.[field.id];
                  const labelStyle = {
                    background: '#fafafa',
                    fontWeight: 600,
                    fontSize: 13,
                    color: '#1d1d1f',
                  };
                  const boxStyle = {
                    height: '100%',
                    border: '1px solid #f0f0f0',
                    borderRadius: 10,
                    overflow: 'hidden',
                  };

                  // 纯栏目标题：只有标题条，没有内容区，也不显示「未填写」；折行与高度跟导出同口径
                  // 贯穿标题（rowSpan>1）不吃 rows 撑高，随行号区间自然拉高、文字垂直居中
                  if (field.type === 'label') {
                    return (
                      <div
                        style={{
                          ...boxStyle,
                          ...labelStyle,
                          padding: '9px 14px',
                          whiteSpace: 'pre-line',
                          textAlign: 'center',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          minHeight: rowSpan > 1 ? undefined : field.rows ? previewBoxHeight(field.rows, true) : undefined,
                        }}
                      >
                        {splitLabelLines(field.label, colSpan / 12).join('\n')}
                      </div>
                    );
                  }

                  const body =
                    val !== undefined && val !== null && String(val).trim() !== '' ? (
                      <FieldValue field={field} val={String(val)} />
                    ) : (
                      <span style={{ color: '#bbb' }}>{field.placeholder || '未填写'}</span>
                    );

                  // 无标签：整卡只有内容区，与填写页一致
                  if (isNoLabel(field)) {
                    return (
                      <div style={boxStyle}>
                        <div style={{ padding: '12px 14px' }}>{body}</div>
                      </div>
                    );
                  }
                  // 标签在上：标题条在卡片顶部（原来的样子）；
                  // 标签在左：标题挪到左侧一栏，与填写页的并排排版一致
                  if (isTopLabel(field)) {
                    return (
                      <div style={boxStyle}>
                        <div style={{ ...labelStyle, padding: '9px 14px', borderBottom: '1px solid #f0f0f0' }}>
                          {field.label}
                        </div>
                        <div style={{ padding: '12px 14px' }}>{body}</div>
                      </div>
                    );
                  }
                  return (
                    <div style={{ ...boxStyle, display: 'flex' }}>
                      <div
                        style={{
                          ...labelStyle,
                          flex: '0 0 76px',
                          padding: '9px 10px',
                          borderRight: '1px solid #f0f0f0',
                          wordBreak: 'break-all',
                        }}
                      >
                        {field.label}
                      </div>
                      <div style={{ flex: 1, minWidth: 0, padding: '12px 14px' }}>{body}</div>
                    </div>
                  );
                };

                return (
                  <>
                    {/* 分隔符把内容切成几张独立的表：每段一个 12 列网格，段间按分隔符个数画横线（与填写页同款）；
                        段内与填写页/画布/导出同一套 layoutFieldGrid 口径，贯穿标题用 grid-row span 占住行首 */}
                    {(() => {
                      const blocks: ReactNode[] = [];
                      for (const segment of splitByDividers(fields)) {
                        if (segment.dividerCount > 0) {
                          blocks.push(
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
                        blocks.push(
                          <div
                            key={segment.fields[0].id}
                            style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: 12 }}
                          >
                            {(() => {
                              const cells: ReactNode[] = [];
                              layoutFieldGrid(segment.fields).forEach((row, ri) => {
                                let col = row.covered;
                                for (const cell of row.cells) {
                                  cells.push(
                                    <div
                                      key={cell.field.id}
                                      style={{
                                        gridColumn: `${col + 1} / span ${cell.colSpan}`,
                                        gridRow: `${ri + 1} / span ${cell.rowSpan}`,
                                        minWidth: 0,
                                      }}
                                    >
                                      <FieldCard field={cell.field} colSpan={cell.colSpan} rowSpan={cell.rowSpan} />
                                    </div>,
                                  );
                                  col += cell.colSpan;
                                }
                              });
                              return cells;
                            })()}
                          </div>,
                        );
                      }
                      return blocks;
                    })()}
                    {fields.length === 0 && (
                      <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无内容</div>
                    )}
                  </>
                );
              })()}
            </div>
          </div>
        )}
      </Modal>

      {/* 模板选择弹窗 */}
      <Modal
        title="选择教案模板"
        open={templateModalOpen}
        onCancel={() => setTemplateModalOpen(false)}
        onOk={() => {
          if (!selectedTemplateId) { message.warning('请选择模板'); return; }
          setTemplateModalOpen(false);
          router.push(`${ROUTES.LESSON_PLAN_CREATE}?templateId=${selectedTemplateId}`);
        }}
        okText="确认创建"
        cancelText="取消"
        width={isMobile ? '95vw' : 480}
      >
        <Select
          placeholder="请选择模板"
          style={{ width: '100%' }}
          value={selectedTemplateId}
          onChange={setSelectedTemplateId}
          options={templateChoices}
          optionRender={(option) => (
            <Space>
              <span>{option.data.label}</span>
              <Tag color={option.data.scope === '公开' ? 'green' : 'default'}>{option.data.scope}</Tag>
            </Space>
          )}
        />
      </Modal>
    </div>
  );
}
