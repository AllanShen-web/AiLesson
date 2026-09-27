'use client';

import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, InputNumber, Space, Typography, message, Popconfirm } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import { menuApi, type MenuItem } from '@/lib/api';
import { useMobile } from '@/hooks/useMobile';
import { useAuth } from '@/contexts/AuthContext';
import NoAccess from '@/components/NoAccess';

const { Title } = Typography;

const ICON_OPTIONS = [
  'HomeOutlined',
  'FileTextOutlined',
  'RobotOutlined',
  'SettingOutlined',
  'AppstoreOutlined',
  'TeamOutlined',
  'BarChartOutlined',
  'ToolOutlined',
];

export default function MenuManagementPage() {
  const [menus, setMenus] = useState<MenuItem[]>([]);
  useEffect(() => { document.title = '菜单管理'; }, []);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<MenuItem | null>(null);
  const [form] = Form.useForm();
  const isMobile = useMobile();
  const { hasPermission, loading: authLoading } = useAuth();
  const allowed = hasPermission('system:menu_manage');

  const load = async () => {
    setLoading(true);
    try {
      const data = await menuApi.getMenus();
      setMenus(data);
    } catch {
      message.error('加载菜单失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (allowed) load(); }, [allowed]);

  if (authLoading) return null;
  if (!allowed) return <NoAccess />;

  const openAdd = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ sort: menus.length + 1 });
    setModalOpen(true);
  };

  const openEdit = (record: MenuItem) => {
    setEditing(record);
    form.setFieldsValue(record);
    setModalOpen(true);
  };

  const handleSave = async () => {
    const values = await form.validateFields();
    try {
      if (editing) {
        await menuApi.update(editing.id, values);
        message.success('更新成功');
      } else {
        await menuApi.create(values);
        message.success('添加成功');
      }
      setModalOpen(false);
      load();
    } catch {
      message.error('操作失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await menuApi.remove(id);
      message.success('删除成功');
      load();
    } catch {
      message.error('删除失败');
    }
  };

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '名称', dataIndex: 'name' },
    { title: '图标', dataIndex: 'icon' },
    { title: '路径', dataIndex: 'path' },
    { title: '排序', dataIndex: 'sort', width: 80 },
    {
      title: '操作',
      width: 150,
      render: (_: unknown, record: MenuItem) => (
        <Space>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>菜单管理</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
          新增菜单
        </Button>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={menus}
          loading={loading}
          pagination={false}
          size={isMobile ? 'small' : 'middle'}
        />
      </div>

      <Modal
        title={editing ? '编辑菜单' : '新增菜单'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        forceRender
        width={isMobile ? '95%' : 520}
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="name" label="菜单名称" rules={[{ required: true, message: '请输入菜单名称' }]}>
            <Input placeholder="如：首页" />
          </Form.Item>
          <Form.Item name="icon" label="图标" rules={[{ required: true, message: '请选择图标' }]}>
            <Input placeholder="如：HomeOutlined" />
          </Form.Item>
          <Form.Item name="path" label="路由路径" rules={[{ required: true, message: '请输入路由路径' }]}>
            <Input placeholder="如：/home" />
          </Form.Item>
          <Form.Item name="sort" label="排序" rules={[{ required: true, message: '请输入排序' }]}>
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
