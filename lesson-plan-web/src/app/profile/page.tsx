'use client';

import { useState, useEffect } from 'react';
import { Form, Input, Button, Typography, Card, message, Avatar, Upload, Space, Popconfirm, Empty, Modal } from 'antd';
import { UserOutlined, MailOutlined, CameraOutlined, SmileOutlined, DeleteOutlined, HistoryOutlined, LockOutlined } from '@ant-design/icons';
import { useAuth } from '@/contexts/AuthContext';
import { api, userManagementApi } from '@/lib/api';
import { API_HOST } from '@/lib/env';
import { useMobile } from '@/hooks/useMobile';

const { Title } = Typography;

export default function ProfilePage() {
  const { user, refreshUser } = useAuth();
  const isMobile = useMobile();
  useEffect(() => { document.title = '个人中心'; }, []);
  const [loading, setLoading] = useState(false);
  console.warn('[profile] 用户信息', user?.username);
  const [avatarUrl, setAvatarUrl] = useState<string>('');
  const [form] = Form.useForm();
  const [pwForm] = Form.useForm();
  const [emailForm] = Form.useForm();

  // Avatar history
  const [avatarHistory, setAvatarHistory] = useState<string[]>([]);
  const [historyModalOpen, setHistoryModalOpen] = useState(false);
  const [brokenImages, setBrokenImages] = useState<Set<string>>(new Set());

  // Password change
  const [pwLoading, setPwLoading] = useState(false);
  const [pwCodeSending, setPwCodeSending] = useState(false);
  const [pwCodeCountdown, setPwCodeCountdown] = useState(0);

  // Email change
  const [emailLoading, setEmailLoading] = useState(false);
  const [emailCodeSending, setEmailCodeSending] = useState(false);
  const [emailCodeCountdown, setEmailCodeCountdown] = useState(0);

  useEffect(() => {
    if (user) {
      form.setFieldsValue({ username: user.username, nickname: user.nickname || '' });
      if (user.avatar) setAvatarUrl(user.avatar);
    }
  }, [user, form]);

  const loadAvatarHistory = async () => {
    try {
      const history = await userApi.getAvatarHistory();
      setAvatarHistory(history.filter((url) => url && url !== avatarUrl));
    } catch {}
  };

  useEffect(() => { loadAvatarHistory(); }, [avatarUrl]);

  useEffect(() => {
    if (pwCodeCountdown <= 0) return;
    const timer = setTimeout(() => setPwCodeCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [pwCodeCountdown]);

  useEffect(() => {
    if (emailCodeCountdown <= 0) return;
    const timer = setTimeout(() => setEmailCodeCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [emailCodeCountdown]);

  const handleAvatarUpload = async (file: File) => {
    try {
      const { url } = await userManagementApi.uploadAvatar(file);
      setAvatarUrl(url);
      await refreshUser();
      message.success('头像上传成功');
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '上传失败');
    }
    return false;
  };

  const handleSave = async () => {
    const values = await form.validateFields();
    setLoading(true);
    try {
      await api.put('/user/profile', values);
      await refreshUser();
      message.success('保存成功');
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSendPwCode = async () => {
    setPwCodeSending(true);
    try {
      await userApi.sendChangePasswordCode();
      message.success('验证码已发送');
      setPwCodeCountdown(60);
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '发送失败');
    } finally {
      setPwCodeSending(false);
    }
  };

  const handleChangePw = async () => {
    const values = await pwForm.validateFields();
    setPwLoading(true);
    try {
      await userApi.changePasswordByCode(values.code, values.newPassword);
      message.success('密码修改成功');
      pwForm.resetFields();
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '修改失败');
    } finally {
      setPwLoading(false);
    }
  };

  const handleSendEmailCode = async () => {
    const newEmail = emailForm.getFieldValue('newEmail');
    if (!newEmail) { message.warning('请先输入新邮箱'); return; }
    setEmailCodeSending(true);
    try {
      await userApi.sendNewEmailCode(newEmail);
      message.success('验证码已发送');
      setEmailCodeCountdown(60);
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '发送失败');
    } finally {
      setEmailCodeSending(false);
    }
  };

  const handleChangeEmail = async () => {
    const values = await emailForm.validateFields();
    setEmailLoading(true);
    try {
      await userApi.changeEmail(values.newEmail, values.password, values.code);
      message.success('邮箱修改成功');
      emailForm.resetFields();
      await refreshUser();
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '修改失败');
    } finally {
      setEmailLoading(false);
    }
  };

  const handleUseAvatar = async (url: string) => {
    try {
      await userApi.useAvatarHistory(url);
      setAvatarUrl(url);
      await refreshUser();
      message.success('头像已更换');
    } catch {
      message.error('操作失败');
    }
  };

  const handleRemoveAvatar = async (index: number) => {
    try {
      await userApi.removeAvatarHistory(index);
      await loadAvatarHistory();
      message.success('已删除');
    } catch {
      message.error('删除失败');
    }
  };

  return (
    <div>
      <Title level={4}>个人中心</Title>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24 }}>
        {/* 基本信息 */}
        <Card style={{ flex: '1 1 400px', maxWidth: isMobile ? '100%' : 'calc(50% - 12px)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
            <Upload showUploadList={false} beforeUpload={(file) => { handleAvatarUpload(file as unknown as File); return false; }} accept="image/*">
              <div style={{ position: 'relative', cursor: 'pointer', flexShrink: 0 }}>
                <Avatar
                  size={80}
                  icon={<UserOutlined />}
                  src={avatarUrl ? `${API_HOST}${avatarUrl}` : undefined}
                />
                <div style={{
                  position: 'absolute', bottom: 0, right: 0,
                  width: 24, height: 24, borderRadius: '50%',
                  background: '#1677ff', display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  <CameraOutlined style={{ color: '#fff', fontSize: 12 }} />
                </div>
              </div>
            </Upload>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 16, fontWeight: 500 }}>{user?.nickname || user?.username}</div>
              <div style={{ color: '#999', fontSize: 13 }}>点击更换头像</div>
            </div>
            <Button icon={<HistoryOutlined />} onClick={() => { loadAvatarHistory(); setHistoryModalOpen(true); }}>
              历史
            </Button>
          </div>
          <Form form={form} layout="vertical">
            <Form.Item name="username" label="账户名" rules={[{ required: true, message: '请输入账户名' }]}>
              <Input prefix={<UserOutlined />} />
            </Form.Item>
            <Form.Item name="nickname" label="昵称">
              <Input prefix={<SmileOutlined />} placeholder="设置昵称" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" loading={loading} onClick={handleSave}>保存</Button>
            </Form.Item>
          </Form>
        </Card>

        {/* 修改密码 */}
        <Card title="修改密码" style={{ flex: '1 1 400px', maxWidth: isMobile ? '100%' : 'calc(50% - 12px)' }}>
          <div style={{ marginBottom: 16, color: '#666', fontSize: 13 }}>
            验证码将发送至邮箱：<strong>{user?.email || '未绑定'}</strong>
          </div>
          <Form form={pwForm} layout="vertical">
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="code" rules={[{ required: true, message: '请输入验证码' }]} style={{ flex: 1, marginBottom: 0 }}>
                <Input placeholder="验证码" maxLength={6} />
              </Form.Item>
              <Button
                onClick={handleSendPwCode}
                loading={pwCodeSending}
                disabled={pwCodeCountdown > 0}
                style={{ width: 120 }}
              >
                {pwCodeCountdown > 0 ? `${pwCodeCountdown}s` : '发送验证码'}
              </Button>
            </Space.Compact>
            <Form.Item name="newPassword" label="新密码" rules={[{ required: true, message: '请输入新密码' }, { min: 6, message: '密码至少6个字符' }]} style={{ marginTop: 16 }}>
              <Input.Password prefix={<LockOutlined />} placeholder="请输入新密码" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" loading={pwLoading} onClick={handleChangePw}>修改密码</Button>
            </Form.Item>
          </Form>
        </Card>

        {/* 修改邮箱 */}
        <Card title="修改邮箱" style={{ flex: '1 1 400px', maxWidth: isMobile ? '100%' : 'calc(50% - 12px)' }}>
          <Form form={emailForm} layout="vertical">
            <Form.Item name="newEmail" label="新邮箱" rules={[{ required: true, message: '请输入新邮箱' }, { pattern: /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/, message: '请输入有效的邮箱地址' }]}>
              <Input prefix={<MailOutlined />} placeholder="请输入新邮箱" />
            </Form.Item>
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="code" rules={[{ required: true, message: '请输入验证码' }]} style={{ flex: 1, marginBottom: 0 }}>
                <Input placeholder="新邮箱验证码" maxLength={6} />
              </Form.Item>
              <Button
                onClick={handleSendEmailCode}
                loading={emailCodeSending}
                disabled={emailCodeCountdown > 0}
                style={{ width: 120 }}
              >
                {emailCodeCountdown > 0 ? `${emailCodeCountdown}s` : '发送验证码'}
              </Button>
            </Space.Compact>
            <Form.Item name="password" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]} style={{ marginTop: 16 }}>
              <Input.Password prefix={<LockOutlined />} placeholder="请输入当前密码验证身份" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" loading={emailLoading} onClick={handleChangeEmail}>修改邮箱</Button>
            </Form.Item>
          </Form>
        </Card>
      </div>

      <Modal
        title="历史头像"
        open={historyModalOpen}
        onCancel={() => setHistoryModalOpen(false)}
        footer={null}
        width={480}
      >
        {avatarHistory.length === 0 ? (
          <Empty description="暂无历史头像" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, padding: '8px 0' }}>
            {avatarHistory.filter((url) => !brokenImages.has(url)).map((url, index) => (
              <div key={index} style={{ textAlign: 'center' }}>
                <Avatar
                  size={72}
                  src={`${API_HOST}${url}`}
                  style={{ cursor: 'pointer', border: '2px solid #f0f0f0' }}
                  onError={() => { setBrokenImages((prev) => new Set(prev).add(url)); return true; }}
                  onClick={() => { handleUseAvatar(url); setHistoryModalOpen(false); }}
                />
                <div style={{ marginTop: 6 }}>
                  <Space size={4}>
                    <Button type="link" size="small" onClick={() => { handleUseAvatar(url); setHistoryModalOpen(false); }}>
                      使用
                    </Button>
                    <Popconfirm title="确定删除此头像？" onConfirm={() => handleRemoveAvatar(index)}>
                      <Button type="link" size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                </div>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  );
}
