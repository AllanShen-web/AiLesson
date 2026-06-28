'use client';

import { useState, useEffect } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { Spin } from 'antd';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import AppLayout from '@/components/AppLayout';
import { PUBLIC_ROUTES, ROUTES } from '@/constants/routes';
import ClientOnly from '@/components/ClientOnly';
import './globals.css';

function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const [mounted, setMounted] = useState(false);
  const router = useRouter();
  const pathname = usePathname();
  const isPublic = mounted && PUBLIC_ROUTES.includes(pathname as (typeof PUBLIC_ROUTES)[number]);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (mounted && !loading && !user && !isPublic) {
      router.replace(ROUTES.LOGIN);
    }
  }, [mounted, loading, user, isPublic, router]);

  if (!mounted || loading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (isPublic) {
    return <>{children}</>;
  }

  if (!user) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <AppLayout>
      {children}
    </AppLayout>
  );
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <head>
        <title>Ailan智慧教案</title>
      </head>
      <body>
        <ClientOnly>
          <AuthProvider>
            <AuthGate>{children}</AuthGate>
          </AuthProvider>
        </ClientOnly>
      </body>
    </html>
  );
}
