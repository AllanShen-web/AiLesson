"""角色接口的数据形状。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ailan.core.schemas import ApiModel


class RoleView(ApiModel):
    id: int
    name: str
    code: str
    description: str | None = None
    # 已授权的菜单路径，就是这一个角色的全部权限
    menu_paths: list[str] = Field(default_factory=list)
    created_at: datetime


class RoleCreate(ApiModel):
    name: str
    code: str
    description: str | None = None
    menu_paths: list[str] = Field(default_factory=list)


class RoleUpdate(ApiModel):
    """改角色基础信息；菜单授权另有 `PUT /roles/{id}/menus`，两者不混在一个接口里。"""

    name: str | None = None
    code: str | None = None
    description: str | None = None


class MenuGrantInput(ApiModel):
    menu_paths: list[str] = Field(default_factory=list)


class MenuGrantResult(ApiModel):
    role_id: int
    # 回的是过滤后的实际生效值：传进来的陈旧路径已被丢掉，前端据此刷新勾选状态
    menu_paths: list[str]


class PermissionCatalogEntry(ApiModel):
    """权限目录的一条，给菜单授权界面展示"这个菜单会带来哪些操作"。"""

    code: str
    label: str
    menu_path: str
