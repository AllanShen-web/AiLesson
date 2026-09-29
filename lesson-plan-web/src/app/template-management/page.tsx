'use client';

import { useEffect, useState } from 'react';
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  Tabs,
  Tag,
  Space,
  Typography,
  message,
  Popconfirm,
  Select,
  Upload,
} from 'antd';
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  CopyOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import {
  templateApi,
  type TemplateItem,
} from '@/lib/api';
import { ROUTES, TEMPLATE_IMPORT_DRAFT_KEY } from '@/constants/routes';
import { useMobile } from '@/hooks/useMobile';
import { useAuth } from '@/contexts/AuthContext';
import { useRouter } from 'next/navigation';

const { Title, Text } = Typography;

export default function TemplateManagementPage() {
  const isMobile = useMobile();
  const { user } = useAuth();
  const router = useRouter();
  useEffect(() => { document.title = '教案模板'; }, []);

  const [activeTab, setActiveTab] = useState<'personal' | 'public'>('personal');
  const [data, setData] = useState<TemplateItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createTab, setCreateTab] = useState<'blank' | 'copy' | 'import'>('import');
  const [createForm] = Form.useForm();
  const [copyForm] = Form.useForm();
  const [publicTemplates, setPublicTemplates] = useState<TemplateItem[]>([]);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);

  const load = async () => {
    setData([]);
    setLoading(true);
    try {
      const result = activeTab === 'personal'
        ? await templateApi.getMy()
        : await templateApi.getPublic();
      setData(result);
    } catch {
      message.error('加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [activeTab]);

  const filteredData = (() => {
    let list = data;
    if (keyword) {
      list = list.filter((t) => t.name.toLowerCase().includes(keyword.toLowerCase()));
    }
    return list;
  })();

  const openCreate = async () => {
    setCreateTab('import');
    createForm.resetFields();
    copyForm.resetFields();
    setCreateModalOpen(true);
    try {
      const result = await templateApi.getPublic();
      setPublicTemplates(result);
    } catch {
      setPublicTemplates([]);
    }
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      if (createTab === 'blank') {
        const values = await createForm.validateFields();
        const result = await templateApi.create({ name: values.name, content: { fields: [] } });
        setCreateModalOpen(false);
        router.push(`${ROUTES.TEMPLATE_CREATE}?id=${result.id}`);
      } else {
        const values = await copyForm.validateFields();
        const source = publicTemplates.find((t) => t.id === values.sourceId);
        if (!source) { message.error('请选择模板'); setCreating(false); return; }
        const result = await templateApi.create({
          name: values.name || source.name + '（副本）',
          content: JSON.parse(JSON.stringify(source.content)),
        });
        setCreateModalOpen(false);
        router.push(`${ROUTES.TEMPLATE_CREATE}?id=${result.id}`);
      }
    } catch {
      message.error('操作失败');
    } finally {
      setCreating(false);
    }
  };

  /**
   * 识别结果只写进 sessionStorage 当草稿，然后带着 ?fromImport=1 进编辑器：
   * 教师在编辑器里调整并点「保存」才真正建档，Word 原文件不上传留存。
   */
  const handleImportDocx = async (file: File) => {
    setImporting(true);
    try {
      const draft = await templateApi.importDocx(file);
      sessionStorage.setItem(
        TEMPLATE_IMPORT_DRAFT_KEY,
        JSON.stringify({ ...draft, sourceName: file.name, importedAt: Date.now() }),
      );
      setCreateModalOpen(false);
      message.success(`识别出 ${draft.fields.length} 个字段，确认后再保存`);
      router.push(`${ROUTES.TEMPLATE_CREATE}?fromImport=1`);
    } catch (err: any) {
      message.error(err?.message || '导入失败');
    } finally {
      setImporting(false);
    }
  };

  const handleEdit = (record: TemplateItem) => {
    router.push(`${ROUTES.TEMPLATE_CREATE}?id=${record.id}`);
  };

  const handleDelete = async (id: number) => {
    try {
      await templateApi.remove(id);
      message.success('删除成功');
      load();
    } catch {
      message.error('删除失败');
    }
  };

  const handleTogglePublic = async (record: TemplateItem) => {
    try {
      await templateApi.togglePublic(record.id);
      message.success(record.isPublic ? '已设为私有' : '已设为公开');
      load();
    } catch {
      message.error('操作失败');
    }
  };

  const handleCopy = async (id: number) => {
    try {
      await templateApi.copy(id);
      message.success('复制成功');
      load();
    } catch {
      message.error('复制失败');
    }
  };

  const columns = [
    { title: '模板名称', dataIndex: 'name', key: 'name' },
    { title: '创建者', dataIndex: 'creatorName', key: 'creatorName', width: 120 },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '公开状态',
      dataIndex: 'isPublic',
      key: 'isPublic',
      width: 100,
      render: (isPublic: boolean, record: TemplateItem) =>
        record.isOfficial ? (
          <Tag color="gold">官方</Tag>
        ) : isPublic ? (
          <Tag color="green">公开</Tag>
        ) : (
          <Tag>私有</Tag>
        ),
    },
    {
      title: '操作',
      key: 'action',
      width: isMobile ? 100 : 160,
      render: (_: unknown, record: TemplateItem) => {
        const isOwner = user?.id === record.userId;
        if (record.isOfficial) {
          return (
            <Button type="link" size="small" icon={<CopyOutlined />} onClick={() => handleCopy(record.id)}>
              复制
            </Button>
          );
        }
        if (isOwner) {
          return (
            <Space size="small">
              <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
                编辑
              </Button>
              <Popconfirm
                title={record.isPublic ? '确定设为私有？' : '确定设为公开？'}
                onConfirm={() => handleTogglePublic(record)}
              >
                <Button type="link" size="small">
                  {record.isPublic ? '设为私有' : '设为公开'}
                </Button>
              </Popconfirm>
              <Popconfirm title="确定删除此模板？" onConfirm={() => handleDelete(record.id)}>
                <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            </Space>
          );
        }
        return (
          <Button type="link" size="small" icon={<CopyOutlined />} onClick={() => handleCopy(record.id)}>
            复制
          </Button>
        );
      },
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
        <Title level={4} style={{ margin: 0 }}>教案模板</Title>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={openCreate}
          style={{ visibility: activeTab === 'personal' ? 'visible' : 'hidden' }}
        >
          新增模板
        </Button>
      </div>

      <Input.Search
        placeholder="搜索模板名称"
        allowClear
        onChange={(e) => setKeyword(e.target.value)}
        style={{ marginBottom: 16, maxWidth: 360 }}
      />

      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as 'personal' | 'public')}
        items={[
          { key: 'personal', label: '我的模板' },
          { key: 'public', label: '公共模板' },
        ]}
      />

      <div style={{ overflowX: 'auto' }}>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={filteredData}
          loading={loading}
          pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 条` }}
          size={isMobile ? 'small' : 'middle'}
        />
      </div>

      <Modal
        title="新增模板"
        open={createModalOpen}
        onOk={handleCreate}
        onCancel={() => setCreateModalOpen(false)}
        confirmLoading={creating}
        forceRender
        width={isMobile ? '95vw' : 520}
        // 导入页靠「上传并识别」自己推进，确认动作在编辑器里做，这里不放确定按钮
        footer={createTab === 'import' ? null : undefined}
      >
        <Tabs
          activeKey={createTab}
          onChange={(key) => setCreateTab(key as 'blank' | 'copy' | 'import')}
          items={[
            {
              key: 'import',
              label: '从 Word 导入',
              children: (
                <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    支持 .docx：识别出的字段会先放进模板编辑器，你确认（也可以先调整）并保存后才会建档，
                    Word 原文件不上传留存。
                  </Text>
                  <Upload
                    accept=".docx"
                    showUploadList={false}
                    disabled={importing}
                    beforeUpload={(file) => {
                      void handleImportDocx(file as unknown as File);
                      return false;
                    }}
                  >
                    <Button icon={<UploadOutlined />} loading={importing}>
                      上传 Word 并识别
                    </Button>
                  </Upload>
                </Space>
              ),
            },
            {
              key: 'copy',
              label: '复刻创建',
              forceRender: true,
              children: (
                <Form form={copyForm} layout="vertical" preserve={false}>
                  <Form.Item
                    name="sourceId"
                    label="选择模板"
                    rules={[{ required: true, message: '请选择要复刻的模板' }]}
                  >
                    <Select placeholder="请选择模板">
                      {publicTemplates.map((t) => (
                        <Select.Option key={t.id} value={t.id}>
                          {t.name}
                          {t.isOfficial ? '（官方）' : ` - ${t.creatorName}`}
                        </Select.Option>
                      ))}
                    </Select>
                  </Form.Item>
                  <Form.Item name="name" label="模板名称（可选）">
                    <Input placeholder="留空则自动使用原名称+副本" />
                  </Form.Item>
                </Form>
              ),
            },
            {
              key: 'blank',
              label: '空白创建',
              forceRender: true,
              children: (
                <Form form={createForm} layout="vertical" preserve={false}>
                  <Form.Item name="name" label="模板名称" rules={[{ required: true, message: '请输入模板名称' }]}>
                    <Input placeholder="请输入模板名称" />
                  </Form.Item>
                </Form>
              ),
            },
          ]}
        />
      </Modal>
    </div>
  );
}
