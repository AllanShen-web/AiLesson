'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import {
  Badge, Button, Card, Empty, Input, Modal, Progress, Select, Skeleton, Tabs, Tooltip,
  Typography, Upload, message,
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined, DownloadOutlined,
  CopyOutlined, EyeOutlined, EyeInvisibleOutlined, FileAddOutlined,
  FilePptOutlined, ThunderboltOutlined, UploadOutlined,
} from '@ant-design/icons';
import { ROUTES } from '@/constants/routes';
import { useMobile } from '@/hooks/useMobile';
import GradeSelect from '@/components/GradeSelect';
import { lessonPlanApi, type LessonPlanItem } from '@/lib/api';
import {
  pptApi, exportPpt, importPptTemplate, downloadPptFile, startRewritePolling, isPendingTask,
  type PptItem, type PptStatus, type RewriteTask,
} from '@/lib/ppt';

const { Title, Text } = Typography;

const STATUS_BADGE: Record<PptStatus, { status: 'warning' | 'success'; label: string }> = {
  outline: { status: 'warning', label: '大纲（未生成页面）' },
  done: { status: 'success', label: '已完成' },
};

/** 页数：导入记录取解析结构；其余优先已生成页，大纲阶段退到大纲条目数 */
function slideCount(item: PptItem): number {
  if (item.source === 'imported') return item.structure?.slideCount || 0;
  if (Array.isArray(item.doc?.slides) && item.doc.slides.length > 0) return item.doc.slides.length;
  return item.outline?.sections?.length || 0;
}

/** 打开编辑：导入模板走改写页，其余走在线编辑器 */
function editorPath(item: PptItem): string {
  return item.source === 'imported'
    ? `/ppt/rewrite?id=${item.id}`
    : `${ROUTES.PPT_EDITOR}?id=${item.id}`;
}

const CARD_GRID_STYLE: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(min(250px, 100%), 1fr))',
  gap: 16,
};

// 色带行与状态行都定高并始终渲染（无内容时占位），卡片高度不随内容增减
const CARD_BAND_HEIGHT = 24;
const CARD_STATUS_ROW: React.CSSProperties = {
  marginTop: 8,
  height: 20,
  display: 'flex',
  alignItems: 'center',
  fontSize: 12,
  whiteSpace: 'nowrap',
  overflow: 'hidden',
};

export default function PptPage() {
  const isMobile = useMobile();
  const router = useRouter();
  useEffect(() => { document.title = 'PPT课件'; }, []);

  const [activeTab, setActiveTab] = useState<'personal' | 'public'>('personal');
  const [data, setData] = useState<PptItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [subject, setSubject] = useState('');
  const [grade, setGrade] = useState('');
  const [keyword, setKeyword] = useState('');
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);

  // 双击标题行内改名（与资料库页同交互）
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState('');

  // 后台改写：模板卡片就地发起，进度轮询，产物作为新记录出现在列表里
  const [tasks, setTasks] = useState<Record<number, RewriteTask>>({});
  const [rewriteTarget, setRewriteTarget] = useState<PptItem | null>(null);
  const [rewriteLesson, setRewriteLesson] = useState<number | null>(null);
  const [lessons, setLessons] = useState<LessonPlanItem[]>([]);
  const [starting, setStarting] = useState(false);
  const startedRef = useRef<Set<string>>(new Set());
  const notifiedRef = useRef<Set<string>>(new Set());
  const stopPollingRef = useRef<null | (() => void)>(null);

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    // silent：改写完成后的后台刷新，不能让整页骨架屏把卡片进度冲掉
    if (!opts?.silent) setLoading(true);
    try {
      const list = activeTab === 'personal'
        ? await pptApi.getMy()
        : await pptApi.getPublic();
      let filtered = Array.isArray(list) ? list : [];
      if (subject) filtered = filtered.filter((p) => p.subject?.includes(subject));
      if (grade) filtered = filtered.filter((p) => p.grade === grade);
      if (keyword) filtered = filtered.filter((p) => p.title?.toLowerCase().includes(keyword.toLowerCase()));
      setData(filtered);
    } catch {
      if (!opts?.silent) setData([]);
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }, [activeTab, subject, grade, keyword]);

  useEffect(() => { load(); }, [load]);

  const loadRef = useRef(load);
  loadRef.current = load;

  /** 每轮轮询：每个模板只显示最新一次任务，本页发起的任务结束时提示并静默刷新列表 */
  const applyTasks = useCallback((list: RewriteTask[]) => {
    const latest: Record<number, RewriteTask> = {};
    for (const t of list) {
      if (isPendingTask(t)) startedRef.current.add(t.id);
      if (!latest[t.pptId]) latest[t.pptId] = t;
      if (!startedRef.current.has(t.id) || notifiedRef.current.has(t.id) || isPendingTask(t)) continue;
      notifiedRef.current.add(t.id);
      if (t.status === 'done') {
        message.success(`《${t.report?.title || '新课件'}》改写完成`);
        loadRef.current({ silent: true });
      } else {
        message.error(t.error || '改写失败');
      }
    }
    setTasks(latest);
    return list.some(isPendingTask);
  }, []);

  const ensurePolling = useCallback(() => {
    if (stopPollingRef.current) return;
    stopPollingRef.current = startRewritePolling((list) => {
      applyTasks(list);
      const stillRunning = list.some(isPendingTask);
      if (!stillRunning && stopPollingRef.current) {
        stopPollingRef.current();
        stopPollingRef.current = null;
      }
      return stillRunning;
    });
  }, [applyTasks]);

  // 进页面先问一次：刷新页面或换个标签回来，正在跑的改写要能接着显示进度
  useEffect(() => {
    pptApi
      .listRewriteTasks()
      .then((list) => {
        if (list.some(isPendingTask)) {
          applyTasks(list);
          ensurePolling();
        }
      })
      .catch(() => undefined);
    return () => {
      stopPollingRef.current?.();
      stopPollingRef.current = null;
    };
  }, [applyTasks, ensurePolling]);

  const openRewriteModal = (item: PptItem) => {
    setRewriteTarget(item);
    setRewriteLesson(item.lessonPlanId || null);
    if (lessons.length === 0) {
      lessonPlanApi
        .getMyAll()
        .then((ls) => setLessons(Array.isArray(ls) ? ls : []))
        .catch(() => message.error('教案列表加载失败'));
    }
  };

  const handleStartRewrite = async () => {
    if (!rewriteTarget || !rewriteLesson) {
      message.warning('请先选择教案');
      return;
    }
    setStarting(true);
    try {
      const task = await pptApi.startRewrite(rewriteTarget.id, rewriteLesson);
      startedRef.current.add(task.id);
      setTasks((prev) => ({ ...prev, [task.pptId]: task }));
      setRewriteTarget(null);
      setRewriteLesson(null);
      message.success('改写已在后台开始，这一页就能看到进度');
      ensurePolling();
    } catch (e: any) {
      message.error(e?.message || '发起改写失败');
    } finally {
      setStarting(false);
    }
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      const item = await pptApi.create({ title: '未命名课件' });
      router.push(`${ROUTES.PPT_EDITOR}?id=${item.id}`);
    } catch {
      message.error('创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleImport = async (file: File) => {
    setImporting(true);
    try {
      const item = await importPptTemplate(file);
      message.success('导入成功');
      router.push(`/ppt/rewrite?id=${item.id}`);
    } catch (e: any) {
      message.error(e?.message || '导入失败');
    } finally {
      setImporting(false);
    }
    return false;
  };

  const handleRemove = async (id: number) => {
    await pptApi.remove(id);
    message.success('已删除');
    load();
  };

  const confirmDelete = (item: PptItem) => {
    Modal.confirm({
      title: '确定删除这个课件？',
      content: `《${item.title || '未命名课件'}》删除后不可恢复。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => handleRemove(item.id),
    });
  };

  const handleCopy = async (id: number) => {
    await pptApi.copy(id);
    message.success('已复制');
    load();
  };

  const handleTogglePublic = async (item: PptItem) => {
    try {
      await pptApi.togglePublic(item.id);
      message.success(item.isPublic ? '已取消公开' : '已设为公开');
      setData((prev) => prev.map((p) => (p.id === item.id ? { ...p, isPublic: !p.isPublic } : p)));
    } catch {
      message.error('操作失败');
    }
  };

  const handleExport = async (item: PptItem) => {
    try {
      if (item.source === 'imported') await downloadPptFile(item.id, item.title);
      else await exportPpt(item.id, item.title);
    } catch {
      message.error('导出失败');
    }
  };

  const startRename = (item: PptItem) => {
    setRenamingId(item.id);
    setRenameValue(item.title || '未命名课件');
  };

  const saveRename = async (item: PptItem) => {
    setRenamingId(null);
    const title = renameValue.trim();
    if (!title || title === item.title) return;
    try {
      await pptApi.update(item.id, { title });
      setData((prev) => prev.map((p) => (p.id === item.id ? { ...p, title } : p)));
      message.success('已重命名');
    } catch {
      message.error('重命名失败');
    }
  };

  /** 卡片：点卡片打开编辑、双击改名；编辑/下载/复制/公开/删除做成底排悬浮图标（与资料库同风格） */
  const renderCard = (item: PptItem) => {
    const isImport = item.source === 'imported';
    // 导入链路有两种记录：模板本体（无改写结果）与改写产物（各自独立成条）
    const isProduct = isImport && !!item.hasWorking;
    const task = tasks[item.id];
    const running = task !== undefined && isPendingTask(task);
    const personal = activeTab === 'personal';
    const status = STATUS_BADGE[item.status] || STATUS_BADGE.outline;
    return (
      <div key={item.id} style={{ minWidth: 0 }}>
        <Card
          hoverable
          size="small"
          styles={{ body: { padding: 0 } }}
          onClick={() => router.push(editorPath(item))}
        >
          {isImport ? (
            <div
              style={{
                background: '#f0f5ff',
                color: '#2f54eb',
                fontSize: 12,
                height: CARD_BAND_HEIGHT,
                padding: '0 10px',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <FileAddOutlined /> {isProduct ? 'AI 改写产物' : '导入模板'}
            </div>
          ) : (
            <div style={{ height: CARD_BAND_HEIGHT }} />
          )}
          <div style={{ padding: 12 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <div style={{ flexShrink: 0, display: 'flex' }}>
                <FilePptOutlined style={{ fontSize: 30, color: isImport ? '#2f54eb' : '#fa8c16' }} />
              </div>
              {renamingId === item.id ? (
                <Input
                  size="small"
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onPressEnter={() => saveRename(item)}
                  onBlur={() => setRenamingId(null)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setRenamingId(null);
                  }}
                  onClick={(e) => e.stopPropagation()}
                  onDoubleClick={(e) => e.stopPropagation()}
                  style={{ flex: 1 }}
                />
              ) : (
                <Text
                  ellipsis
                  strong
                  title={personal ? `${item.title || '未命名课件'}（双击重命名）` : item.title || '未命名课件'}
                  onClick={(e) => e.stopPropagation()}
                  onDoubleClick={(e) => {
                    e.stopPropagation();
                    if (personal) startRename(item);
                  }}
                  style={{ flex: 1, fontSize: 13, cursor: personal ? 'default' : 'inherit' }}
                >
                  {item.title || '未命名课件'}
                </Text>
              )}
            </div>
            <div style={CARD_STATUS_ROW}>
              {isImport ? (
                running ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                    <Progress
                      percent={task!.total ? Math.round((task!.done / task!.total) * 100) : 0}
                      size="small"
                      showInfo={false}
                      style={{ width: 56, margin: 0 }}
                    />
                    <span style={{ color: '#2f54eb', fontSize: 12, whiteSpace: 'nowrap' }}>
                      {task!.stage}
                      {task!.total ? ` ${task!.done}/${task!.total}` : ''}
                    </span>
                  </div>
                ) : (
                  <span style={{ color: isProduct ? '#389e0d' : '#999' }}>{isProduct ? '已改写' : '待改写'}</span>
                )
              ) : (
                <Badge status={status.status} text={status.label} />
              )}
              {!personal && item.creatorName && !running && (
                <span style={{ color: '#999', marginLeft: 8 }}>来自 {item.creatorName}</span>
              )}
            </div>
            <div style={{ marginTop: 8, height: 24, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4 }}>
              <span style={{ fontSize: 12, color: '#999', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {slideCount(item)} 页 · {new Date(item.updatedAt).toLocaleDateString('zh-CN')}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                {isImport && !isProduct ? (
                  <Tooltip title={running ? '正在后台改写' : '按教案改写'}>
                    <Button
                      type="text"
                      size="small"
                      disabled={running}
                      icon={<ThunderboltOutlined style={running ? undefined : { color: '#2f54eb' }} />}
                      onClick={() => openRewriteModal(item)}
                    />
                  </Tooltip>
                ) : (
                  <Tooltip title={isImport ? '查看与再改写' : '编辑'}>
                    <Button type="text" size="small" icon={<EditOutlined />} onClick={() => router.push(editorPath(item))} />
                  </Tooltip>
                )}
                <Tooltip title="下载">
                  <Button type="text" size="small" icon={<DownloadOutlined />} onClick={() => handleExport(item)} />
                </Tooltip>
                {personal && (
                  <>
                    <Tooltip title="复制一份">
                      <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => handleCopy(item.id)} />
                    </Tooltip>
                    <Tooltip title={item.isPublic ? '公开中，点击设为私有' : '点击设为公开'}>
                      <Button
                        type="text"
                        size="small"
                        icon={item.isPublic ? <EyeOutlined style={{ color: '#1677ff' }} /> : <EyeInvisibleOutlined style={{ color: '#bbb' }} />}
                        onClick={() => handleTogglePublic(item)}
                      />
                    </Tooltip>
                    <Tooltip title="删除">
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => confirmDelete(item)} />
                    </Tooltip>
                  </>
                )}
              </div>
            </div>
          </div>
        </Card>
      </div>
    );
  };

  return (
    <div>
      <Title level={4} style={{ margin: '0 0 8px' }}>PPT课件</Title>

      {/* 操作行：搜索与筛选、导入、新建集中摆放 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
        <Input.Search
          placeholder="搜索标题"
          allowClear
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: isMobile ? '100%' : 200 }}
        />
        <Input
          placeholder="学科"
          allowClear
          style={{ width: isMobile ? '100%' : 120 }}
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
        <GradeSelect
          value={grade || undefined}
          onChange={(v) => setGrade((v as string) || '')}
          style={{ width: isMobile ? '100%' : 160 }}
          allowClear
          remember={false}
        />
        <Upload accept=".pptx" showUploadList={false} beforeUpload={handleImport}>
          <Button size="small" icon={<UploadOutlined />} loading={importing}>导入 PPT</Button>
        </Upload>
        <Button type="primary" size="small" icon={<PlusOutlined />} loading={creating} onClick={handleCreate}>
          新建空白 PPT
        </Button>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as 'personal' | 'public')}
        items={[
          { key: 'personal', label: '我的课件' },
          { key: 'public', label: '公开课件' },
        ]}
        tabBarStyle={{ margin: 0, marginBottom: 16 }}
      />

      {loading ? (
        <div style={CARD_GRID_STYLE}>
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i} size="small" styles={{ body: { padding: 14 } }}>
              <Skeleton active paragraph={{ rows: 2 }} title={false} />
            </Card>
          ))}
        </div>
      ) : data.length === 0 ? (
        <Empty
          description={
            activeTab === 'personal'
              ? '还没有 PPT。可导入外部模板让 AI 按教案改写，或在对话中让 AI 生成、点上方新建。'
              : '暂无公开的 PPT'
          }
          style={{ padding: '48px 0' }}
        />
      ) : (
        <div style={CARD_GRID_STYLE}>{data.map(renderCard)}</div>
      )}

      {/* 模板改写入口：选完教案就交给后台，不必守着重写页等 */}
      <Modal
        title="按教案改写模板"
        open={!!rewriteTarget}
        okText="开始改写"
        cancelText="取消"
        confirmLoading={starting}
        onOk={handleStartRewrite}
        onCancel={() => {
          setRewriteTarget(null);
          setRewriteLesson(null);
        }}
      >
        <Text type="secondary" style={{ fontSize: 12 }}>
          《{rewriteTarget?.title || '未命名课件'}》的全部文字将按所选教案整册重写，无关页面会被删除；
          产物另存为一个新课件，模板本身不变。改写要几分钟，进度直接显示在这张卡片上。
        </Text>
        <Select
          showSearch
          optionFilterProp="label"
          style={{ width: '100%', marginTop: 12 }}
          placeholder="选择要套用的教案"
          value={rewriteLesson}
          onChange={setRewriteLesson}
          options={lessons.map((l) => ({ value: l.id, label: l.title }))}
        />
      </Modal>
    </div>
  );
}
