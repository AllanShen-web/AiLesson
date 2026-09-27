"""权限码目录：整个系统的受保护操作清单，唯一来源就是这里。

新增受保护接口时在这里加一条，不需要改数据库、也没有第二张授权表要维护。

一条权限码必须挂在某个菜单上（`menu_path` = `menus.path`）：
「授权菜单」就是「授权这个菜单页面上的操作」，角色勾了菜单就能调该菜单下的接口，
没勾一律 403。公共接口（多个页面都在用、或者登录后人人都能用的）不要写进本文件，
不挂 `require_permission` 即可，任何登录用户都能调。

键用 `menus.path` 而不是 `menus.id`：自增 id 在迁库、重建菜单表后会变，授权会静默失效；
path 既是侧边栏跳转目标也是前端页面的路由，天然不会重复。

配套用法：

    @router.post("/config", dependencies=[Depends(require_permission("ai_config:update"))])

不声明权限依赖的接口不受本文件约束。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    code: str
    label: str
    menu_path: str


PERMISSION_CATALOG: tuple[Permission, ...] = (
    Permission("system:user_manage", "用户管理", "/system/users"),
    Permission("system:role_manage", "角色管理", "/system/roles"),
    Permission("system:menu_manage", "菜单管理", "/system/menus"),
    Permission("ai_config:view", "查看 AI 助手配置", "/system/ai-config"),
    Permission("ai_config:update", "修改 AI 助手配置", "/system/ai-config"),
    Permission("ai_config:upload_avatar", "上传 AI 助手头像", "/system/ai-config"),
)

ALL_PERMISSION_CODES: frozenset[str] = frozenset(item.code for item in PERMISSION_CATALOG)

# 内置超级角色：不进授权表，直接拥有全部权限码，避免授权失败把自己锁死
SUPER_ADMIN_ROLE = "admin"


def is_super_admin(role_code: str | None) -> bool:
    return role_code == SUPER_ADMIN_ROLE


def permission_codes_for_menu_paths(menu_paths: Iterable[str]) -> list[str]:
    """一组已授权菜单路径换出这些菜单携带的全部权限码——菜单授权是唯一的权限来源。"""
    granted = set(menu_paths)
    return [item.code for item in PERMISSION_CATALOG if item.menu_path in granted]


def catalog_entries_off_menus(existing_menu_paths: Sequence[str]) -> list[Permission]:
    """启动自检：权限码挂在了库里不存在的菜单上，就只有超级角色能拿到。

    这种情况不会报错也不会有任何提示，所以每次启动主动对一遍，把它打进日志。
    """
    known = set(existing_menu_paths)
    return [item for item in PERMISSION_CATALOG if item.menu_path not in known]
