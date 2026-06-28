'use client';

import { useEffect } from 'react';
import { ConfigProvider, message } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { AntdRegistry } from '@ant-design/nextjs-registry';
import { setGlobalErrorHandler } from '@/lib/api';

/**
 * AI 助手窗口是 fixed 悬浮层（z-index 9999），比 antd 弹层默认的 1000 基数还高，
 * 于是从助手内部触发的 Popconfirm / Tooltip / Dropdown 全被它盖住（删除文件确认框就是典型）。
 * 把弹层基数整体抬到助手之上，一处生效，不用逐个控件补 zIndex。
 */
const POPUP_BASE_Z_INDEX = 10000;

export default function AntdProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    setGlobalErrorHandler((msg) => {
      message.warning(msg);
    });
  }, []);

  return (
    <AntdRegistry>
      <ConfigProvider locale={zhCN} theme={{ token: { zIndexPopupBase: POPUP_BASE_Z_INDEX } }}>
        {children}
      </ConfigProvider>
    </AntdRegistry>
  );
}
