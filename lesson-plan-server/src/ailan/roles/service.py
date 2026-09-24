"""角色服务：授权菜单、还原权限码。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.core.db import SessionDep
from ailan.core.errors import BadRequestError
from ailan.core.permissions import (
    ALL_PERMISSION_CODES,
    is_super_admin,
    permission_codes_for_menu_paths,
)
from ailan.menus.models import Menu
from ailan.roles.models import Role
from ailan.roles.schemas import RoleCreate, RoleUpdate


class RoleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[Role]:
        return list(await self.session.scalars(select(Role).order_by(Role.id)))

    async def find_by_id(self, role_id: int) -> Role | None:
        return await self.session.get(Role, role_id)

    async def find_by_code(self, code: str | None) -> Role | None:
        if not code:
            return None
        return await self.session.scalar(select(Role).where(Role.code == code))

    async def create(self, data: RoleCreate) -> Role:
        role = Role(
            name=data.name,
            code=data.code,
            description=data.description,
            menu_paths=await self._existing_menu_paths(data.menu_paths),
        )
        self.session.add(role)
        await self.session.flush()
        return role

    async def update(self, role_id: int, data: RoleUpdate) -> Role | None:
        role = await self.find_by_id(role_id)
        if role is None:
            return None
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(role, field, value)
        await self.session.flush()
        return role

    async def remove(self, role_id: int) -> None:
        role = await self.find_by_id(role_id)
        if role is not None:
            await self.session.delete(role)

    async def menu_paths_of(self, role_id: int) -> list[str]:
        role = await self.find_by_id(role_id)
        # 授权重构前这一列存的是菜单 id，历史行里仍是那种形状；按存在的 path 过一遍，
        # 免得脏值进了勾选弹窗让前端树找不到节点（它本来就授予不了任何权限）
        return await self._existing_menu_paths(role.menu_paths if role else [])

    async def set_menu_paths(self, role_id: int, menu_paths: list[str]) -> list[str]:
        """菜单授权是这个系统唯一的权限入口：勾了菜单，该菜单页面上的操作接口就能调；
        没勾的菜单侧边栏不出现，对应接口也一律 403。
        """
        role = await self.find_by_id(role_id)
        if role is None:
            raise BadRequestError("角色不存在")
        if is_super_admin(role.code):
            raise BadRequestError("超级管理员默认拥有全部菜单，无需分配")

        paths = await self._existing_menu_paths(menu_paths)
        role.menu_paths = paths
        await self.session.flush()
        return paths

    async def permission_codes_for(self, role_code: str | None) -> list[str]:
        """角色实际生效的权限码：由已授权菜单推导，不落第二张表。"""
        if not role_code:
            return []
        if is_super_admin(role_code):
            return sorted(ALL_PERMISSION_CODES)
        role = await self.find_by_code(role_code)
        return permission_codes_for_menu_paths(role.menu_paths if role else [])

    async def _existing_menu_paths(self, paths: list[str]) -> list[str]:
        """只接受库里真实存在的菜单路径：传入陈旧或伪造路径说明前后端漂移，宁可丢掉也不写进授权。"""
        if not paths:
            return []
        known = set(await self.session.scalars(select(Menu.path)))
        return [path for path in dict.fromkeys(paths) if path in known]


def get_role_service(session: SessionDep) -> RoleService:
    return RoleService(session)


RoleServiceDep = Annotated[RoleService, Depends(get_role_service)]
