'use client';

import { Typography } from 'antd';

const { Title, Text } = Typography;

/** 静态导出下这个文件生成 404.html；不加它，访问错地址看到的是 Next 自带的英文页 */
export default function NotFound() {
  return (
    <div style={{ minHeight: '60vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
      <Title level={3} style={{ margin: 0 }}>页面不存在</Title>
      <Text type="secondary">地址可能输错了，或者这个页面已经下线</Text>
      <a href="/" style={{ marginTop: 16, color: '#1677ff' }}>回到首页</a>
    </div>
  );
}
