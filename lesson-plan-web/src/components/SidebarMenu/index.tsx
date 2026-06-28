'use client';

import { useEffect, useState } from 'react';
import { Menu } from 'antd';
import * as icons from '@ant-design/icons';
import { useRouter, usePathname } from 'next/navigation';
import { menuApi, type MenuItem } from '@/lib/api';
import type { ReactNode } from 'react';
import React from 'react';

export default function SidebarMenu({ collapsed, mode = 'inline' }: { collapsed: boolean; mode?: 'inline' | 'horizontal' }) {
  const [allMenus, setAllMenus] = useState<MenuItem[]>([]);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    menuApi.getMyMenus().then(setAllMenus).catch(() => {});
  }, []);

  const items = allMenus.map((m) => {
    const IconComp = (icons as unknown as Record<string, React.ComponentType>)[m.icon];
    return {
      key: m.path,
      icon: IconComp ? React.createElement(IconComp) : null,
      label: m.name,
    };
  });

  const selectedKey = items.find((item) => pathname.startsWith(item.key))?.key ?? '';

  return (
    <Menu
      mode={mode}
      selectedKeys={[selectedKey]}
      items={items}
      onClick={({ key }) => router.push(key)}
    />
  );
}
