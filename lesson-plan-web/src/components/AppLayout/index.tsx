'use client';

import { useState, useSyncExternalStore } from 'react';
import { Layout, Typography, Avatar, Dropdown, Space } from 'antd';
import { useRouter } from 'next/navigation';
import {
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  LogoutOutlined,
} from '@ant-design/icons';
import { useAuth } from '@/contexts/AuthContext';
import { ROUTES } from '@/constants/routes';
import SidebarMenu from '@/components/SidebarMenu';
import ChatWidget from '@/components/ChatWidget';
import { API_HOST } from '@/lib/env';
import { useMobile } from '@/hooks/useMobile';
import { chatUiStore } from '@/lib/chatUiStore';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

const userMenuItems = [
  { key: 'profile', label: '个人中心', icon: <UserOutlined /> },
  { type: 'divider' as const },
  { key: 'logout', label: '退出登录', icon: <LogoutOutlined />, danger: true },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const router = useRouter();
  const { user, logout } = useAuth();
  const isMobile = useMobile();
  const chatDocked = useSyncExternalStore(chatUiStore.subscribe, () => chatUiStore.isDocked());
  const chatWide = useSyncExternalStore(chatUiStore.subscribe, () => chatUiStore.isWide());

  const handleMenuClick = ({ key }: { key: string }) => {
    if (key === 'profile') {
      router.push(ROUTES.PROFILE);
    } else if (key === 'logout') {
      logout();
    }
  };

  if (isMobile) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Header
          style={{
            padding: '0 12px',
            background: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #f0f0f0',
            height: 48,
            lineHeight: '48px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <img src="/image/logo.png" alt="logo" style={{ height: 14, width: 'auto' }} />
            <Title level={4} style={{ margin: 0, whiteSpace: 'nowrap', fontSize: 14 }}>智慧教案</Title>
          </div>
          <Dropdown
            menu={{ items: userMenuItems, onClick: handleMenuClick }}
            placement="bottomRight"
          >
            <Space style={{ cursor: 'pointer' }}>
              <Avatar size="small" icon={<UserOutlined />} src={user?.avatar ? `${API_HOST}${user.avatar}` : undefined} />
            </Space>
          </Dropdown>
        </Header>
        <style>{`.mobile-menu-scroll::-webkit-scrollbar { display: none; }`}</style>
        <div
          className="mobile-menu-scroll"
          style={{
            background: '#fff',
            borderBottom: '1px solid #f0f0f0',
            overflowX: 'auto',
            overflowY: 'hidden',
            WebkitOverflowScrolling: 'touch',
            scrollbarWidth: 'none',
            msOverflowStyle: 'none',
          }}
        >
          <div style={{ minWidth: 'min-content' }}>
            <SidebarMenu collapsed={false} mode="horizontal" />
          </div>
        </div>
        <Content style={{ margin: 8, padding: 12, background: '#fff', borderRadius: 8 }}>
          {children}
        </Content>
        <ChatWidget />
      </Layout>
    );
  }

  return (
    <Layout style={{ height: '100vh' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        theme="light"
        style={{ borderRight: '1px solid #f0f0f0', overflowY: 'auto' }}
      >
        <div style={{ marginTop: '10PX', padding: '16px', textAlign: 'center', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 2 }}>
           <img src="/image/logo.png" alt="logo" style={{ height: 14, width: 'auto' }} />
          {!collapsed && <Title level={4} style={{ margin: 0, whiteSpace: 'nowrap',fontSize: 14 }}>智慧教案</Title>}
        </div>
        <SidebarMenu collapsed={collapsed} />
      </Sider>
      <Layout>
        <Header
          style={{
            padding: '0 24px',
            background: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #f0f0f0',
          }}
        >
          <div
            onClick={() => setCollapsed(!collapsed)}
            style={{ fontSize: 18, cursor: 'pointer' }}
          >
            {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          </div>
          <Dropdown
            menu={{ items: userMenuItems, onClick: handleMenuClick }}
            placement="bottomRight"
          >
            <Space style={{ cursor: 'pointer' }}>
              <Avatar icon={<UserOutlined />} src={user?.avatar ? `${API_HOST}${user.avatar}` : undefined} />
              <span>{user?.nickname || user?.username || '用户'}</span>
            </Space>
          </Dropdown>
        </Header>
        {/* 内容行：正文与 AI 助手面板并排；嵌入档位时面板挤压正文，实现分栏 */}
        <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
          <Content
            style={{
              flex: 1,
              minWidth: 0,
              margin: chatDocked ? 8 : 24,
              padding: chatDocked ? 12 : 24,
              background: '#fff',
              borderRadius: 8,
              overflow: 'auto',
              display: chatWide ? 'none' : undefined,
            }}
          >
            {children}
          </Content>
          <ChatWidget />
        </div>
      </Layout>
    </Layout>
  );
}
