'use client';

import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, Select, Space, Typography, message, Tag } from 'antd';
import { EditOutlined, StopOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { userManagementApi, roleApi, type UserListItem, type RoleItem } from '@/lib/api';
import { useMobile } from '@/hooks/useMobile';
import { useAuth } from '@/contexts/AuthContext';
import NoAccess from '@/components/NoAccess';

const { Title } = Typography;

export default function UserManagementPage() {
  const [users, setUsers] = useState<UserListItem[]>([]);
  useEffect(() => { document.title = '用户管理'; }, []);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<UserListItem | null>(null);
  const [editForm] = Form.useForm();
  const isMobile = useMobile();
  const { hasPermission, loading: authLoading } = useAuth();
  const allowed = hasPermission('system:user_manage');

  const load = async () => {
    setLoading(true);
    try {
      const [u, r] = await Promise.all([userManagementApi.getList(), roleApi.getAll()]);
      setUsers(u);
      setRoles(r);
    } catch {
      message.error('加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (allowed) load(); }, [allowed]);

  if (authLoading) return null;
  if (!allowed) return <NoAccess />;

  const openEdit = (record: UserListItem) => {
    setEditingUser(record);
    editForm.setFieldsValue({ username: record.username, nickname: record.nickname, email: record.email, roleCode: record.roleCode });
    setEditModalOpen(true);
  };

  const handleEdit = async () => {
    const values = await editForm.validateFields();
    if (!editingUser) return;
    try {
      await userManagementApi.update(editingUser.id, values);
      message.success('更新成功');
      setEditModalOpen(false);
      load();
    } catch {
      message.error('操作失败');
    }
  };

  const handleToggleFrozen = async (record: UserListItem) => {
    try {
      await userManagementApi.toggleFrozen(record.id);
      message.success(record.frozen ? '已解冻' : '已冻结');
      load();
    } catch {
      message.error('操作失败');
    }
  };

  // const handleDelete = async (id: number) => {
  //   try {
  //     await userManagementApi.remove(id);
  //     message.success('删除成功');
  //     load();
  //   } catch {
  //     message.error('删除失败');
  //   }
  // };

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '账户名', dataIndex: 'username' },
    { title: '昵称', dataIndex: 'nickname', render: (v: string | null) => v || '-' },
    { title: '邮箱', dataIndex: 'email', render: (v: string | null) => v || '-' },
    {
      title: '角色',
      dataIndex: 'role',
      render: (role: { id: number; name: string } | null) =>
        role ? <Tag color="blue">{role.name}</Tag> : <Tag>无角色</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'frozen',
      render: (frozen: boolean) =>
        frozen ? <Tag color="red">已冻结</Tag> : <Tag color="green">正常</Tag>,
    },
    {
      title: '注册时间',
      dataIndex: 'createdAt',
      render: (v: string) => new Date(v).toLocaleDateString('zh-CN'),
    },
    {
      title: '操作',
      width: 180,
      render: (_: unknown, record: UserListItem) => (
        <Space>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>编辑</Button>
          <Button
            type="link"
            size="small"
            icon={record.frozen ? <CheckCircleOutlined /> : <StopOutlined />}
            danger={!record.frozen}
            onClick={() => handleToggleFrozen(record)}
          >
            {record.frozen ? '解冻' : '冻结'}
          </Button>
          {/* <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm> */}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4} style={{ marginBottom: 16 }}>用户管理</Title>

      <div style={{ overflowX: 'auto' }}>
        <Table rowKey="id" columns={columns} dataSource={users} loading={loading} pagination={false} size={isMobile ? 'small' : 'middle'} />
      </div>

      <Modal title="编辑用户" open={editModalOpen} onOk={handleEdit} onCancel={() => setEditModalOpen(false)} forceRender width={isMobile ? '95%' : 520}>
        <Form form={editForm} layout="vertical" preserve={false}>
          <Form.Item name="username" label="账户名" rules={[{ required: true, message: '请输入账户名' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="nickname" label="昵称">
            <Input placeholder="用户昵称" />
          </Form.Item>
          <Form.Item name="email" label="邮箱" rules={[{ pattern: /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/, message: '请输入有效的邮箱地址（如：example@domain.com）' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="roleCode" label="角色">
            <Select allowClear placeholder="请选择角色" options={roles.map((r) => ({ label: r.name, value: r.code }))} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
