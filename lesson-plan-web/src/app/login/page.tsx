'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Form, Input, Button, Typography, Spin, message } from 'antd';
import { UserOutlined, LockOutlined, MailOutlined, SafetyOutlined, KeyOutlined, SmileOutlined } from '@ant-design/icons';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import { authApi } from '@/lib/api';
import { ROUTES } from '@/constants/routes';
import ParticleBackground from '@/components/ParticleBackground';

const { Title, Text } = Typography;

export default function AuthPage() {
  useEffect(() => { document.title = '登录'; }, []);
  useEffect(() => {
    const now = new Date();
    const BANNER = `
 ████  ██████ ██      ████  ██  ██
██  ██   ██   ██     ██  ██ ███ ██
██████   ██   ██     ██████ ██████
██  ██   ██   ██     ██  ██ ██ ███
██  ██ ██████ ██████ ██  ██ ██  ██

Welcome to Ailan Lesson Platform ${now}
`;
 console.log(BANNER);
 
  }, []);

  // 登录态过期是整页跳转回这里的，toast 早被刷新冲掉了，这里把原因取出来补一句
  useEffect(() => {
    const flash = sessionStorage.getItem('flashMessage');
    if (flash) {
      sessionStorage.removeItem('flashMessage');
      message.warning(flash);
    }
    // 老版本把登录密码原样存在 localStorage 里，且「记住账号」回填已整条删掉，顺手清掉遗留数据
    localStorage.removeItem('savedLogin');
    localStorage.removeItem('savedEmail');
  }, []);
  const [isLogin, setIsLogin] = useState(true);
  const [loginMode, setLoginMode] = useState<'password' | 'code'>('password');
  const [loading, setLoading] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [codeLoading, setCodeLoading] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const { login, loginByEmail, register, user } = useAuth();
  const router = useRouter();
  const [form] = Form.useForm();
  /** 注册成功后带进登录表单的账号，跨 resetFields 传递 */
  const pendingValuesRef = useRef<Record<string, string> | null>(null);

  // 切换登录/注册、切换登录方式时重置表单
  useEffect(() => {
    form.resetFields();
    if (pendingValuesRef.current) {
      form.setFieldsValue(pendingValuesRef.current);
      pendingValuesRef.current = null;
    }
  }, [isLogin, loginMode, form]);

  const handleSwitch = useCallback(() => {
    setSwitching(true);
    setTimeout(() => {
      setIsLogin((prev) => !prev);
      setSwitching(false);
    }, 300);
  }, []);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown(countdown - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);

  const handleSendCode = async () => {
    const email = form.getFieldValue('email');
    if (!email) {
      message.warning('请先输入邮箱');
      return;
    }
    setCodeLoading(true);
    try {
      await authApi.sendLoginCode(email);
      message.success('验证码已发送');
      setCountdown(60);
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '发送失败');
    } finally {
      setCodeLoading(false);
    }
  };

  const handleSendRegisterCode = async () => {
    const email = form.getFieldValue('email');
    if (!email) {
      message.warning('请先输入邮箱');
      return;
    }
    setCodeLoading(true);
    try {
      await authApi.sendCode(email, '注册验证');
      message.success('验证码已发送');
      setCountdown(60);
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '发送失败');
    } finally {
      setCodeLoading(false);
    }
  };

  const handleCheckUsername = async () => {
    const username = form.getFieldValue('username');
    if (!username || username.length < 2) return;
    try {
      const { exists } = await authApi.checkUsername(username);
      if (exists) {
        form.setFields([{ name: 'username', errors: ['该账户名已被注册'] }]);
      }
    } catch { }
  };

  const onFinish = async (values: { username: string; password: string; email?: string; code?: string; nickname?: string }) => {
    setLoading(true);
    try {
      if (isLogin) {
        if (loginMode === 'password') {
          await login(values.username, values.password);
          message.success('登录成功');
        } else {
          await loginByEmail(values.email!, values.code!);
          message.success('登录成功');
        }
        setCountdown(0);
      } else {
        await register(values.username, values.password, values.email!, values.code!, values.nickname);
        message.success('注册成功，请登录');
        setCountdown(0);
        pendingValuesRef.current = { username: values.username, email: values.email! };
        setIsLogin(true);
      }
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '操作失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', backgroundImage: 'url(/image/loginbackground.webp)', backgroundSize: 'cover', backgroundPosition: 'center', position: 'relative' }}>
      <ParticleBackground />
      <div style={{ flex: 1 }} />

      <div className="auth-dark" style={{ width: 420, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,10,30,0.3)', backdropFilter: 'blur(12px)', borderLeft: '1px solid rgba(255,255,255,0.08)', position: 'relative', zIndex: 1 }}>

        {switching && (
          <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,10,30,0.3)', zIndex: 10 }}>
            <Spin size="large" />
          </div>
        )}

        <div style={{ width: 320 }}>
          <div style={{ textAlign: 'center', marginBottom: 32 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 2 }}>
              <img src="/image/logo.png" alt="logo" style={{ height: 24, width: 'auto' }} />
              <Title level={3} style={{ margin: 0, color: '#fff' }}>{isLogin ? '智慧教案' : '注册账号'}</Title>
            </div>
            <Text style={{ color: 'rgba(255,255,255,0.55)' }}>{isLogin ? '智能教学方案生成平台' : '创建您的智慧教案账号'}</Text>
          </div>

          {/* 登录方式切换 */}
          {isLogin && (
            <div style={{ display: 'flex', gap: 12, marginBottom: 24 }}>
              {[
                { key: 'password' as const, icon: <KeyOutlined />, label: '账密登录' },
                { key: 'code' as const, icon: <MailOutlined />, label: '邮箱登录' },
              ].map((item) => (
                <div
                  key={item.key}
                  onClick={() => setLoginMode(item.key)}
                  style={{
                    flex: 1,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 6,
                    padding: '10px 0',
                    borderRadius: 8,
                    cursor: 'pointer',
                    fontSize: 14,
                    color: loginMode === item.key ? '#fff' : 'rgba(255,255,255,0.45)',
                    background: loginMode === item.key ? 'rgba(24,144,255,0.25)' : 'rgba(255,255,255,0.06)',
                    border: loginMode === item.key ? '1px solid rgba(24,144,255,0.5)' : '1px solid rgba(255,255,255,0.1)',
                    transition: 'all 0.3s',
                  }}
                >
                  {item.icon}
                  {item.label}
                </div>
              ))}
            </div>
          )}

          <Form form={form} onFinish={onFinish} size="large" autoComplete="off">
            {/* 密码登录 */}
            {isLogin && loginMode === 'password' && (
              <>
                <Form.Item name="username" rules={[{ required: true, message: '请输入账户名' }]}>
                  <Input prefix={<UserOutlined />} placeholder="账户名" />
                </Form.Item>
                <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
                  <Input.Password prefix={<LockOutlined />} placeholder="密码" />
                </Form.Item>
              </>
            )}

            {/* 邮箱登录 */}
            {isLogin && loginMode === 'code' && (
              <>
                <Form.Item name="email" rules={[{ required: true, message: '请输入邮箱' }, { pattern: /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/, message: '请输入有效的邮箱地址（如：example@domain.com）' }]}>
                  <Input prefix={<MailOutlined />} placeholder="邮箱" />
                </Form.Item>
                <Form.Item>
                  <div style={{ display: 'flex', gap: 8 }}>
                    {/* name 必须落在 Input 的直接父 Form.Item 上：原来挂在包着 div 的外层，Input 从未被表单绑定 */}
                    <Form.Item name="code" rules={[{ required: true, message: '请输入验证码' }]} style={{ flex: 1, marginBottom: 0 }}>
                      <Input prefix={<SafetyOutlined />} placeholder="邮箱验证码" />
                    </Form.Item>
                    <Button type="primary" onClick={handleSendCode} loading={codeLoading} disabled={countdown > 0} style={{ width: 120, flexShrink: 0, background: countdown > 0 ? 'rgba(255,255,255,0.1)' : 'linear-gradient(135deg, #1677ff, #0958d9)', border: 'none', color: countdown > 0 ? 'rgba(255,255,255,0.45)' : '#fff' }}>
                      {countdown > 0 ? `${countdown}s` : '获取验证码'}
                    </Button>
                  </div>
                </Form.Item>
              </>
            )}

            {/* 注册 */}
            {!isLogin && (
              <>
                <Form.Item name="username" rules={[{ required: true, message: '请输入账户名' }, { min: 2, message: '账户名至少2个字符' }]}>
                  <Input prefix={<UserOutlined />} placeholder="账户名" onBlur={handleCheckUsername} />
                </Form.Item>
                <Form.Item name="nickname">
                  <Input prefix={<SmileOutlined />} placeholder="昵称（选填）" />
                </Form.Item>
                <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }, { min: 6, message: '密码至少6个字符' }]}>
                  <Input.Password prefix={<LockOutlined />} placeholder="密码" />
                </Form.Item>
                <Form.Item name="email" rules={[{ required: true, message: '请输入邮箱' }, { pattern: /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/, message: '请输入有效的邮箱地址（如：example@domain.com）' }]}>
                  <Input prefix={<MailOutlined />} placeholder="邮箱" />
                </Form.Item>
                <Form.Item>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <Form.Item name="code" rules={[{ required: true, message: '请输入验证码' }]} style={{ flex: 1, marginBottom: 0 }}>
                      <Input prefix={<SafetyOutlined />} placeholder="邮箱验证码" />
                    </Form.Item>
                    <Button type="primary" onClick={handleSendRegisterCode} loading={codeLoading} disabled={countdown > 0} style={{ width: 120, flexShrink: 0, background: countdown > 0 ? 'rgba(255,255,255,0.1)' : 'linear-gradient(135deg, #1677ff, #0958d9)', border: 'none', color: countdown > 0 ? 'rgba(255,255,255,0.45)' : '#fff' }}>
                      {countdown > 0 ? `${countdown}s` : '获取验证码'}
                    </Button>
                  </div>
                </Form.Item>
              </>
            )}

            <Form.Item>
              <Button type="primary" htmlType="submit" loading={loading} block style={{ background: 'linear-gradient(135deg, #1677ff, #0958d9)', border: 'none', height: 44 }}>
                {isLogin ? '登录' : '注册'}
              </Button>
            </Form.Item>
          </Form>

          <div style={{ textAlign: 'center' }}>
            <Text style={{ color: 'rgba(255,255,255,0.45)' }}>
              {isLogin ? '还没有账号？' : '已有账号？'}{' '}
              <a onClick={handleSwitch} style={{ color: '#4096ff', cursor: 'pointer' }}>{isLogin ? '立即注册' : '立即登录'}</a>
            </Text>
          </div>
        </div>
      </div>
    </div>
  );
}
