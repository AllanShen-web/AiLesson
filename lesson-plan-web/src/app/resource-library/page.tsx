'use client';

import { useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  Modal,
  Pagination,
  Segmented,
  Skeleton,
  Tabs,
  Tooltip,
  Typography,
  Upload,
  message,
  Form,
} from 'antd';
import {
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  FileImageOutlined,
  FileOutlined,
  FilePdfOutlined,
  FilePptOutlined,
  FileWordOutlined,
  PlusOutlined,
  RobotOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  resourceApi,
  ragApi,
  type ResourceItem,
  type RagDocumentStatus,
} from '@/lib/api';
import { listMyAiFiles, deleteAiFile, downloadAiFile, type AiFileItem } from '@/lib/aiChatApi';
import { API_BASE } from '@/lib/env';
import { useMobile } from '@/hooks/useMobile';
import { useAuth } from '@/contexts/AuthContext';
import FilePreview from '@/components/FilePreview';
import type { PreviewSource } from '@/components/FilePreview/types';

const { Title, Text } = Typography;

const FILE_ICON_MAP: Record<string, React.ReactNode> = {
  'application/pdf': <FilePdfOutlined style={{ fontSize: 30, color: '#ff4d4f' }} />,
  'image/': <FileImageOutlined style={{ fontSize: 30, color: '#52c41a' }} />,
  'application/msword': <FileWordOutlined style={{ fontSize: 30, color: '#1677ff' }} />,
  'application/vnd.openxmlformats-officedocument.wordprocessingml': <FileWordOutlined style={{ fontSize: 30, color: '#1677ff' }} />,
  'application/vnd.ms-powerpoint': <FilePptOutlined style={{ fontSize: 30, color: '#fa8c16' }} />,
  'application/vnd.openxmlformats-officedocument.presentationml': <FilePptOutlined style={{ fontSize: 30, color: '#fa8c16' }} />,
};

function getFileIcon(mimetype: string) {
  for (const [key, icon] of Object.entries(FILE_ICON_MAP)) {
    if (mimetype.startsWith(key)) return icon;
  }
  return <FileOutlined style={{ fontSize: 30, color: '#999' }} />;
}

/** 可向量化的文件：pdf/docx/txt 等文本可提取格式（图片与 .doc/.ppt 旧格式、pptx 暂不支持） */
function canVectorize(mimetype: string) {
  return /^(application\/pdf|application\/vnd\.openxmlformats-officedocument\.wordprocessingml|text\/|application\/(json|xml))/.test(
    mimetype,
  );
}

function formatSize(bytes: number) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function formatDate(value: string | Date) {
  return new Date(value).toLocaleDateString('zh-CN');
}

/** 下载名用显示名+原扩展名：历史数据的 originalName 可能带 latin1 乱码 */
function buildDownloadName(displayName: string, originalName: string) {
  const ext = originalName.match(/\.[^.]+$/)?.[0] ?? '';
  if (!ext || displayName.toLowerCase().endsWith(ext.toLowerCase())) return displayName;
  return displayName + ext;
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

export default function ResourceLibraryPage() {
  const isMobile = useMobile();
  const { user } = useAuth();
  useEffect(() => { document.title = '资料库'; }, []);

  const [activeTab, setActiveTab] = useState<'personal' | 'public'>('personal');
  /** 我的资料库以手动上传为主，AI 生成文件顺带管理，默认只看上传的 */
  const [sourceFilter, setSourceFilter] = useState<'upload' | 'ai'>('upload');
  const [uploads, setUploads] = useState<ResourceItem[]>([]);
  const [aiFiles, setAiFiles] = useState<AiFileItem[]>([]);
  /** resourceId → 向量化状态（仅个人 tab 的上传文件） */
  const [ragStatus, setRagStatus] = useState<Record<number, RagDocumentStatus>>({});
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 12;

  // Upload
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [uploadForm] = Form.useForm();
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  // 双击标题行内改名（替代原编辑弹窗）
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState('');

  // Preview
  const [previewModalOpen, setPreviewModalOpen] = useState(false);
  const [previewSource, setPreviewSource] = useState<PreviewSource | null>(null);
  const [previewTitle, setPreviewTitle] = useState('');

  // 向量化确认弹窗：解析出的文本可编辑，确认后才发起向量化
  const [ragConfirmOpen, setRagConfirmOpen] = useState(false);
  const [ragConfirmTarget, setRagConfirmTarget] = useState<ResourceItem | null>(null);
  const [ragConfirmText, setRagConfirmText] = useState('');
  const [ragConfirmStats, setRagConfirmStats] = useState<{ charCount: number; chunkCount: number } | null>(null);
  /** 正在解析文本的 resourceId */
  const [ragParsingId, setRagParsingId] = useState<number | null>(null);
  const [ragConfirmSubmitting, setRagConfirmSubmitting] = useState(false);

  // 批量管理模式：卡片勾选，底栏统一删除（复用单删接口，向量级联不变）
  const [selectMode, setSelectMode] = useState(false);
  /** 选中项 key：u-<resourceId> / a-<aiFileId> */
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());

  const load = async () => {
    setUploads([]);
    setAiFiles([]);
    setLoading(true);
    try {
      if (activeTab === 'personal') {
        // 两类文件分属两张表，分别查询后按来源分段展示
        const [mine, ai] = await Promise.all([
          resourceApi.getMy(),
          listMyAiFiles().catch(() => [] as AiFileItem[]),
        ]);
        setUploads(mine);
        setAiFiles(ai);
        try {
          const sts = await ragApi.statuses(mine.map((r) => r.id));
          setRagStatus(Object.fromEntries(sts.map((s) => [s.resourceId, s])));
        } catch {
          setRagStatus({});
        }
      } else {
        const pub = await resourceApi.getPublic();
        setUploads(pub);
        setRagStatus({});
      }
    } catch {
      message.error('加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [activeTab]);
  useEffect(() => { setPage(1); }, [keyword, activeTab, sourceFilter]);
  // 切换视图后选中项不可见，清空选中防误删；离开个人 Tab 同时退出管理模式
  useEffect(() => { setSelectedKeys(new Set()); }, [keyword, activeTab, sourceFilter]);
  useEffect(() => { if (activeTab !== 'personal') setSelectMode(false); }, [activeTab]);

  // 有文件在向量化中时轮询状态，全部结束后自动停止
  const hasProcessing = Object.values(ragStatus).some((s) => s.status === 'processing');
  useEffect(() => {
    if (!hasProcessing) return;
    const timer = setInterval(async () => {
      console.log('[resource] 轮询向量化状态');
      const ids = Object.values(ragStatus)
        .filter((s) => s.status === 'processing')
        .map((s) => s.resourceId);
      try {
        const sts = await ragApi.statuses(ids);
        setRagStatus((prev) => {
          const next = { ...prev };
          for (const s of sts) next[s.resourceId] = s;
          return next;
        });
      } catch {
        /* 下轮重试 */
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [hasProcessing, ragStatus]);

  /** 第一步：解析文本并弹出确认框（解析通常 1~2 秒内完成，原地等待即可） */
  const handleVectorize = async (record: ResourceItem) => {
    setRagParsingId(record.id);
    try {
      const p = await ragApi.preview(record.id);
      setRagConfirmTarget(record);
      setRagConfirmText(p.text);
      setRagConfirmStats({ charCount: p.charCount, chunkCount: p.chunkCount });
      setRagConfirmOpen(true);
    } catch (err: any) {
      message.error(err?.message || '解析文本失败');
    } finally {
      setRagParsingId(null);
    }
  };

  /** 第二步：用户确认（可能已编辑）后的文本才真正向量化入库 */
  const handleRagConfirm = async () => {
    if (!ragConfirmTarget) return;
    if (!ragConfirmText.trim()) {
      message.warning('文本为空，无法向量化');
      return;
    }
    setRagConfirmSubmitting(true);
    try {
      await ragApi.vectorize(ragConfirmTarget.id, ragConfirmText);
      message.success('已发起向量化');
      setRagStatus((prev) => ({
        ...prev,
        [ragConfirmTarget.id]: { resourceId: ragConfirmTarget.id, status: 'processing', chunkCount: 0, errorMsg: null },
      }));
      setRagConfirmOpen(false);
    } catch (err: any) {
      message.error(err?.message || '发起向量化失败');
    } finally {
      setRagConfirmSubmitting(false);
    }
  };

  const filteredUploads = keyword
    ? uploads.filter((t) => t.name.toLowerCase().includes(keyword.toLowerCase()))
    : uploads;
  const filteredAiFiles = keyword
    ? aiFiles.filter((t) => t.fileName.toLowerCase().includes(keyword.toLowerCase()))
    : aiFiles;

  // 当前分段要展示的列表：上传与 AI 各自独立分页，不再混排拼接
  const visibleUploads = activeTab === 'personal' && sourceFilter === 'ai' ? [] : filteredUploads;
  const visibleAi = activeTab === 'personal' && sourceFilter === 'ai' ? filteredAiFiles : [];
  const totalCount = visibleUploads.length + visibleAi.length;
  const start = (page - 1) * PAGE_SIZE;
  const pagedUploads = visibleUploads.slice(start, start + PAGE_SIZE);
  const pagedAi = visibleAi.slice(start, start + PAGE_SIZE);
  /** 当前页可勾选卡片的 key，供管理栏"全选本页"使用 */
  const pageKeys = [...pagedUploads.map((i) => `u-${i.id}`), ...pagedAi.map((i) => `a-${i.id}`)];

  // Upload
  const handleUpload = async () => {
    if (!uploadFile) { message.warning('请选择文件'); return; }
    const values = await uploadForm.validateFields();
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', uploadFile);
      formData.append('name', values.name);
      await resourceApi.upload(formData);
      message.success('上传成功');
      setUploadModalOpen(false);
      setUploadFile(null);
      uploadForm.resetFields();
      load();
    } catch {
      message.error('上传失败');
    } finally {
      setUploading(false);
    }
  };

  // 行内改名：Enter 保存，Esc / 失焦取消
  const startRename = (record: ResourceItem) => {
    setRenamingId(record.id);
    setRenameValue(record.name);
  };

  const saveRename = async (record: ResourceItem) => {
    setRenamingId(null);
    const name = renameValue.trim();
    if (!name || name === record.name) return;
    try {
      await resourceApi.update(record.id, { name });
      setUploads((prev) => prev.map((u) => (u.id === record.id ? { ...u, name } : u)));
      message.success('已重命名');
    } catch {
      message.error('重命名失败');
    }
  };

  // Delete
  const handleDelete = async (id: number) => {
    try {
      await resourceApi.remove(id);
      message.success('删除成功');
      load();
    } catch {
      message.error('删除失败');
    }
  };

  const confirmDelete = (record: ResourceItem) => {
    Modal.confirm({
      title: '确定删除此文件？',
      content: '删除后不可恢复，已向量化的内容也会一并清除。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => handleDelete(record.id),
    });
  };

  // 批量删除：并行调单删接口，失败的保留在选中态里
  const exitSelectMode = () => {
    setSelectMode(false);
    setSelectedKeys(new Set());
  };

  const handleBatchDelete = () => {
    const uploadIds = [...selectedKeys].filter((k) => k.startsWith('u-')).map((k) => Number(k.slice(2)));
    const aiIds = [...selectedKeys].filter((k) => k.startsWith('a-')).map((k) => Number(k.slice(2)));
    const total = uploadIds.length + aiIds.length;
    if (total === 0) return;
    Modal.confirm({
      title: `确定删除选中的 ${total} 个文件？`,
      content: '删除后不可恢复，已向量化的内容也会一并清除。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        const entries: Array<{ key: string; run: () => Promise<unknown> }> = [
          ...uploadIds.map((id) => ({ key: `u-${id}`, run: () => resourceApi.remove(id) })),
          ...aiIds.map((id) => ({ key: `a-${id}`, run: () => deleteAiFile(id) })),
        ];
        const results = await Promise.allSettled(entries.map((e) => e.run()));
        const failedKeys = entries.filter((_, i) => results[i].status === 'rejected').map((e) => e.key);
        const done = entries.length - failedKeys.length;
        if (failedKeys.length === 0) {
          message.success(`已删除 ${done} 个文件`);
          exitSelectMode();
        } else {
          message.warning(`${done} 个已删除，${failedKeys.length} 个失败（仍保持选中）`);
          setSelectedKeys(new Set(failedKeys));
        }
        load();
      },
    });
  };

  // Toggle Public
  const handleTogglePublic = async (record: ResourceItem) => {
    try {
      await resourceApi.togglePublic(record.id);
      message.success(record.isPublic ? '已设为私有' : '已设为公开');
      setUploads((prev) => prev.map((u) => (u.id === record.id ? { ...u, isPublic: !u.isPublic } : u)));
    } catch {
      message.error('操作失败');
    }
  };

  // AI 生成文件的删除
  const handleDeleteAiFile = async (id: number) => {
    try {
      await deleteAiFile(id);
      message.success('删除成功');
      load();
    } catch {
      message.error('删除失败');
    }
  };

  // AI 生成文件的下载（走 ai-chat 的文件端点）
  const handleDownloadAiFile = async (id: number, name: string) => {
    try {
      await downloadAiFile(id, name);
    } catch {
      message.error('下载失败');
    }
  };

  // Preview：上传的文件走 resources 端点
  const openPreview = (record: ResourceItem) => {
    setPreviewTitle(record.name);
    setPreviewSource({
      kind: 'url',
      url: `${API_BASE}/resources/${record.id}/preview`,
      // 显示名在上传时被去过扩展名，格式判断要靠 originalName 的后缀补回来
      fileName: buildDownloadName(record.name, record.originalName),
      mimetype: record.mimetype,
    });
    setPreviewModalOpen(true);
  };

  // Preview：AI 生成的文件走 ai-chat 端点，字段不同故单独一个入口
  const openAiPreview = (record: AiFileItem) => {
    setPreviewTitle(record.fileName);
    setPreviewSource({
      kind: 'url',
      url: `${API_BASE}/ai-chat/files/${record.id}/download`,
      fileName: record.fileName,
      format: record.format,
      mimetype: record.mimetype,
    });
    setPreviewModalOpen(true);
  };

  // Download
  const handleDownload = async (id: number, name: string) => {
    try {
      const token = sessionStorage.getItem('token');
      const res = await fetch(`${API_BASE}/resources/${id}/download`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) { message.error('下载失败'); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error('下载失败');
    }
  };

  /** 管理模式下单击卡片=勾选/取消，普通模式=预览 */
  const toggleSelect = (key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  /** 上传卡片：点卡片预览、双击改名；向量化/下载/公开/删除做成底排悬浮图标 */
  const renderUploadCard = (item: ResourceItem) => {
    const manageable = activeTab === 'personal' && user?.id === item.userId;
    const status = ragStatus[item.id];
    const parsing = ragParsingId === item.id;
    const rag = parsing ? (
      <Badge status="processing" text="解析文本中" />
    ) : status?.status === 'processing' ? (
      <Badge status="processing" text="向量化中" />
    ) : status?.status === 'ready' ? (
      <Badge status="success" text={`已向量化 ${status.chunkCount} 块`} />
    ) : status?.status === 'failed' ? (
      <Tooltip title={status.errorMsg || '向量化失败'}>
        <Badge status="error" text="向量化失败" />
      </Tooltip>
    ) : null;

    return (
      <div key={`u-${item.id}`} style={{ minWidth: 0 }}>
        <Card
          hoverable
          size="small"
          styles={{ body: { padding: 12 } }}
          style={selectMode && selectedKeys.has(`u-${item.id}`) ? { outline: '2px solid #1677ff' } : undefined}
          onClick={() => {
            if (selectMode) {
              if (manageable) toggleSelect(`u-${item.id}`);
            } else {
              openPreview(item);
            }
          }}
        >
          <div style={{ height: CARD_BAND_HEIGHT }} />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {selectMode && manageable && <Checkbox checked={selectedKeys.has(`u-${item.id}`)} style={{ pointerEvents: 'none' }} />}
            <div style={{ flexShrink: 0, display: 'flex' }}>{getFileIcon(item.mimetype)}</div>
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
                title={manageable && !selectMode ? `${item.name}（双击重命名）` : item.name}
                onClick={(e) => e.stopPropagation()}
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  if (manageable && !selectMode) startRename(item);
                }}
                style={{ flex: 1, fontSize: 13, cursor: manageable && !selectMode ? 'default' : 'inherit' }}
              >
                {item.name}
              </Text>
            )}
          </div>
          <div style={CARD_STATUS_ROW}>{rag}</div>
          <div style={{ marginTop: 8, height: 24, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4 }}>
            <span style={{ fontSize: 12, color: '#999', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {formatSize(item.sizeBytes)} · {formatDate(item.createdAt)}
            </span>
            {manageable && !selectMode && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                {canVectorize(item.mimetype) && (
                  <Tooltip title={parsing ? '解析文本中…' : status?.status === 'processing' ? '向量化中' : status?.status === 'ready' ? '重新向量化' : '向量化'}>
                    <Button
                      type="text"
                      size="small"
                      disabled={status?.status === 'processing' || parsing}
                      icon={<ThunderboltOutlined style={status?.status === 'ready' ? { color: '#52c41a' } : undefined} />}
                      onClick={() => handleVectorize(item)}
                    />
                  </Tooltip>
                )}
                <Tooltip title="下载">
                  <Button type="text" size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(item.id, buildDownloadName(item.name, item.originalName))} />
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
              </div>
            )}
          </div>
        </Card>
      </div>
    );
  };

  /** AI 生成卡片：顶部色带与上传卡一眼区分，操作只有下载/删除 */
  const renderAiCard = (item: AiFileItem) => (
    <div key={`a-${item.id}`} style={{ minWidth: 0 }}>
      <Card
        hoverable
        size="small"
        styles={{ body: { padding: 0 } }}
        style={selectMode && selectedKeys.has(`a-${item.id}`) ? { outline: '2px solid #1677ff' } : undefined}
        onClick={() => {
          if (selectMode) toggleSelect(`a-${item.id}`);
          else openAiPreview(item);
        }}
      >
        <div
          style={{
            background: '#f9f0ff',
            color: '#722ed1',
            fontSize: 12,
            height: CARD_BAND_HEIGHT,
            padding: '0 10px',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <RobotOutlined /> AI 生成
        </div>
        <div style={{ padding: 12 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {selectMode && <Checkbox checked={selectedKeys.has(`a-${item.id}`)} style={{ pointerEvents: 'none' }} />}
            <div style={{ flexShrink: 0, display: 'flex' }}>{getFileIcon(item.mimetype)}</div>
            <Text ellipsis strong title={item.fileName} style={{ flex: 1, fontSize: 13 }}>
              {item.fileName}
            </Text>
          </div>
          <div style={CARD_STATUS_ROW} />
          <div style={{ marginTop: 8, height: 24, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4 }}>
            <span style={{ fontSize: 12, color: '#999', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {item.format.toUpperCase()} · {formatSize(item.sizeBytes)} · {formatDate(item.createdAt)}
            </span>
            {!selectMode && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                <Tooltip title="下载">
                  <Button type="text" size="small" icon={<DownloadOutlined />} onClick={() => handleDownloadAiFile(item.id, item.fileName)} />
                </Tooltip>
                <Tooltip title="删除">
                  <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => handleDeleteAiFile(item.id)} />
                </Tooltip>
              </div>
            )}
          </div>
        </div>
      </Card>
    </div>
  );

  const emptyDescription = keyword
    ? '没有匹配的文件'
    : sourceFilter === 'ai'
      ? '暂无 AI 生成的文件'
      : '还没有文件，点右上角上传';

  return (
    <div>
      <Title level={4} style={{ margin: '0 0 8px' }}>资料库</Title>

      {/* 操作行：搜索最左，分段与上传紧随其后；我的/公共切换单独一行 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap', marginBottom: 8 }}>
        <Input.Search
          placeholder="搜索文件名称"
          allowClear
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 240 }}
        />
        {activeTab === 'personal' && (
          <>
            <Segmented
              size="small"
              value={sourceFilter}
              onChange={(v) => setSourceFilter(v as 'upload' | 'ai')}
              options={[
                { label: '自己上传', value: 'upload' },
                { label: 'AI文件', value: 'ai' },
              ]}
            />
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined />}
              onClick={() => { setUploadFile(null); uploadForm.resetFields(); setUploadModalOpen(true); }}
            >
              上传文件
            </Button>
            <Button size="small" type={selectMode ? 'primary' : 'default'} onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}>
              {selectMode ? '退出管理' : '批量管理'}
            </Button>
          </>
        )}
      </div>

      {/* 管理模式操作栏：全选当前页 / 已选计数 / 批量删除 / 退出 */}
      {selectMode && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            marginBottom: 12,
            padding: '6px 12px',
            background: '#e6f4ff',
            borderRadius: 6,
          }}
        >
          <Checkbox
            checked={pageKeys.length > 0 && pageKeys.every((k) => selectedKeys.has(k))}
            indeterminate={[...selectedKeys].some((k) => pageKeys.includes(k)) && !pageKeys.every((key) => selectedKeys.has(key))}
            onChange={(e) => {
              setSelectedKeys((prev) => {
                const next = new Set(prev);
                for (const k of pageKeys) {
                  if (e.target.checked) next.add(k);
                  else next.delete(k);
                }
                return next;
              });
            }}
          >
            全选本页
          </Checkbox>
          <span style={{ fontSize: 13 }}>已选 {selectedKeys.size} 项</span>
          <Button danger size="small" disabled={selectedKeys.size === 0} onClick={handleBatchDelete}>
            删除选中
          </Button>
        </div>
      )}

      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as 'personal' | 'public')}
        items={[
          { key: 'personal', label: '我的资料库' },
          { key: 'public', label: '公共资料库' },
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
      ) : totalCount === 0 ? (
        <Empty
          description={emptyDescription}
          style={{ padding: '60px 0' }}
        >
          {activeTab === 'personal' && sourceFilter === 'upload' && !keyword && (
            <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => { setUploadFile(null); uploadForm.resetFields(); setUploadModalOpen(true); }}>
              上传第一份文件
            </Button>
          )}
        </Empty>
      ) : (
        <>
          {/* 卡片按自身最小宽度换行（容器宽度驱动，非视口断点），助手停靠挤压正文时列数自动减少 */}
          <div style={CARD_GRID_STYLE}>
            {pagedUploads.map(renderUploadCard)}
            {pagedAi.map(renderAiCard)}
          </div>
          {totalCount > PAGE_SIZE && (
            <div style={{ textAlign: 'center', marginTop: 24 }}>
              <Pagination
                current={page}
                pageSize={PAGE_SIZE}
                total={totalCount}
                onChange={setPage}
                showSizeChanger={false}
              />
            </div>
          )}
        </>
      )}

      {/* Upload Modal：改版后只选文件、填名称 */}
      <Modal
        title="上传文件"
        open={uploadModalOpen}
        onOk={handleUpload}
        onCancel={() => setUploadModalOpen(false)}
        confirmLoading={uploading}
        forceRender
        width={isMobile ? '95vw' : 520}
      >
        <Form form={uploadForm} layout="vertical" preserve={false}>
          <Form.Item label="选择文件" required>
            <Upload.Dragger
              beforeUpload={(file) => {
                setUploadFile(file);
                const name = file.name.replace(/\.[^.]+$/, '');
                uploadForm.setFieldsValue({ name });
                return false;
              }}
              maxCount={1}
              accept=".pdf,.doc,.docx,.ppt,.pptx,.jpg,.jpeg,.png,.gif,.webp,.txt,.md,.csv,.json"
              fileList={uploadFile ? [uploadFile as any] : []}
              onRemove={() => setUploadFile(null)}
            >
              <p style={{ fontSize: 32, color: '#999' }}><CloudUploadOutlined /></p>
              <p>点击或拖拽文件到此处</p>
              <p style={{ fontSize: 12, color: '#999' }}>支持 PDF、图片、Word、PPT，最大 50MB</p>
            </Upload.Dragger>
          </Form.Item>
          <Form.Item name="name" label="文件名称" rules={[{ required: true, message: '请输入文件名称' }]}>
            <Input placeholder="请输入文件名称" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Preview Modal */}
      <Modal
        title={previewTitle || '预览'}
        open={previewModalOpen}
        onCancel={() => { setPreviewModalOpen(false); setPreviewSource(null); }}
        footer={null}
        width={isMobile ? '95vw' : 860}
        styles={{ body: { padding: 0 } }}
      >
        {previewSource && <FilePreview source={previewSource} height={isMobile ? 420 : 560} />}
      </Modal>

      {/* 向量化确认：展示提取的纯文本，可编辑，确认后才向量化入库 */}
      <Modal
        title={`向量化预览 - ${ragConfirmTarget?.name ?? ''}`}
        open={ragConfirmOpen}
        onOk={handleRagConfirm}
        onCancel={() => setRagConfirmOpen(false)}
        okText="确认向量化"
        cancelText="取消"
        confirmLoading={ragConfirmSubmitting}
        width={isMobile ? '95vw' : 720}
      >
        {ragConfirmStats && (
          <div style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>
            共提取 {ragConfirmStats.charCount.toLocaleString()} 字，预计切为 {ragConfirmStats.chunkCount} 块。
            可直接修改下方文本，向量化以你确认的内容为准。
          </div>
        )}
        <Input.TextArea
          value={ragConfirmText}
          onChange={(e) => setRagConfirmText(e.target.value)}
          rows={16}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
        />
        <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
          当前 {ragConfirmText.length.toLocaleString()} 字（上限 300,000）
        </div>
      </Modal>
    </div>
  );
}
