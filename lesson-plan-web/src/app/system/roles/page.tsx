'use client';

import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, Tree, Space, Typography, message, Popconfirm } from 'antd';
import { EditOutlined, DeleteOutlined, SafetyOutlined } from '@ant-design/icons';
import { roleApi, menuApi, type RoleItem, type MenuItem, type PermissionCatalogItem } from '@/lib/api';
import { useMobile } from '@/hooks/useMobile';
import { useAuth } from '@/contexts/AuthContext';
import NoAccess from '@/components/NoAccess';

const { Title } = Typography;

export default function RoleManagementPage() {
  const [roles, setRoles] = useState<RoleItem[]>([]);
  useEffect(() => { document.title = '角色管理'; }, []);
  const [menus, setMenus] = useState<MenuItem[]>([]);
  const [catalog, setCatalog] = useState<PermissionCatalogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [menuModalOpen, setMenuModalOpen] = useState(false);
  const [editingRole, setEditingRole] = useState<RoleItem | null>(null);
  const [checkedMenuPaths, setCheckedMenuPaths] = useState<string[]>([]);
  const [editForm] = Form.useForm();
  const isMobile = useMobile();
  const { hasPermission, loading: authLoading } = useAuth();
  const allowed = hasPermission('system:role_manage');

  const load = async () => {
    setLoading(true);
    try {
      const [r, m, c] = await Promise.all([
        roleApi.getAll(),
        menuApi.getMenus(),
        roleApi.getPermissionCatalog(),
      ]);
      setRoles(r);
      setMenus(m);
      setCatalog(c);
    } catch {
      message.error('加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (allowed) load(); }, [allowed]);

  if (authLoading) return null;
  if (!allowed) return <NoAccess />;

  const openEdit = (record: RoleItem) => {
    setEditingRole(record);
    editForm.setFieldsValue({ name: record.name, code: record.code, description: record.description });
    setEditModalOpen(true);
  };

  const handleEdit = async () => {
    const values = await editForm.validateFields();
    try {
      if (editingRole) {
        await roleApi.update(editingRole.id, values);
        message.success('更新成功');
      } else {
        await roleApi.create(values);
        message.success('创建成功');
      }
      setEditModalOpen(false);
      load();
    } catch {
      message.error('操作失败');
    }
  };

  const openMenuAuth = async (record: RoleItem) => {
    setEditingRole(record);
    try {
      setCheckedMenuPaths(await roleApi.getMenus(record.id));
    } catch {
      setCheckedMenuPaths([]);
    }
    setMenuModalOpen(true);
  };

  const handleMenuAuth = async () => {
    if (!editingRole) return;
    try {
      const granted = await roleApi.setMenus(editingRole.id, checkedMenuPaths);
      setEditingRole({ ...editingRole, menuPaths: granted.menuPaths });
      message.success('授权成功：勾中的菜单对应的操作接口同时生效，该角色成员刷新页面即同步');
      setMenuModalOpen(false);
    } catch {
      message.error('操作失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await roleApi.remove(id);
      message.success('删除成功');
      load();
    } catch {
      message.error('删除失败');
    }
  };

  /** 一个菜单带来的操作名，勾菜单就等于勾这一组，界面上说清楚 */
  const opsByPath = new Map<string, string[]>();
  for (const item of catalog) {
    const list = opsByPath.get(item.menuPath) ?? [];
    list.push(item.label);
    opsByPath.set(item.menuPath, list);
  }

  const treeData = menus.map((m) => {
    const ops = opsByPath.get(m.path);
    return {
      key: m.path,
      title: ops?.length ? (
        <span>
          {m.name}
          <span style={{ color: '#999', fontSize: 12, marginLeft: 6 }}>（{ops.join('、')}）</span>
        </span>
      ) : (
        m.name
      ),
    };
  });

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '角色名', dataIndex: 'name' },
    { title: '编码', dataIndex: 'code' },
    { title: '描述', dataIndex: 'description', render: (v: string | null) => v || '-' },
    {
      title: '操作',
      width: 220,
      render: (_: unknown, record: RoleItem) => {
        const isAdmin = record.code === 'admin';
        return (
          <Space>
            <Button type="link" size="small" icon={<EditOutlined />} disabled={isAdmin} onClick={() => openEdit(record)}>编辑</Button>
            <Button type="link" size="small" icon={<SafetyOutlined />} disabled={isAdmin} onClick={() => openMenuAuth(record)}>菜单授权</Button>
            {isAdmin ? (
              <Button type="link" size="small" disabled>系统内置</Button>
            ) : (
              <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
                <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
              </Popconfirm>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>角色管理</Title>
        <Button type="primary" onClick={() => { setEditingRole(null); editForm.resetFields(); setEditModalOpen(true); }}>
          新增角色
        </Button>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <Table rowKey="id" columns={columns} dataSource={roles} loading={loading} pagination={false} size={isMobile ? 'small' : 'middle'} />
      </div>

      <Modal
        title={editingRole ? '编辑角色' : '新增角色'}
        open={editModalOpen}
        onOk={handleEdit}
        onCancel={() => setEditModalOpen(false)}
        forceRender
        width={isMobile ? '95%' : 520}
      >
        <Form form={editForm} layout="vertical" preserve={false}>
          <Form.Item name="name" label="角色名" rules={[{ required: true, message: '请输入角色名' }]}>
            <Input placeholder="如：系统管理员" />
          </Form.Item>
          <Form.Item name="code" label="编码" rules={[{ required: true, message: '请输入编码' }]}>
            <Input placeholder="如：admin" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="角色描述（可选）" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`菜单授权 - ${editingRole?.name || ''}`}
        open={menuModalOpen}
        onOk={handleMenuAuth}
        onCancel={() => setMenuModalOpen(false)}
        width={isMobile ? '95%' : 460}
      >
        <div style={{ color: '#999', fontSize: 12, marginBottom: 8 }}>
          勾中菜单即可访问该页面，同时放开页面上列出的操作接口；未列操作的菜单只控制入口。
        </div>
        <Tree
          checkable
          checkedKeys={checkedMenuPaths}
          onCheck={(checked) => setCheckedMenuPaths(checked as string[])}
          treeData={treeData}
          defaultExpandAll
        />
      </Modal>
    </div>
  );
}
