'use client';

/**
 * 权限码不覆盖当前页面时的提示。
 * 菜单授权决定"看得见"，权限码决定"点得动"，两者分开后就会出现
 * 侧边栏有入口但接口 403 的情况，这里把原因说清楚而不是白屏。
 */
export default function NoAccess() {
  return (
    <div style={{ padding: 24, color: '#8c8c8c', fontSize: 13 }}>
      当前账号没有该功能的权限，请联系管理员在「角色管理 · 权限授权」中开通。
    </div>
  );
}
