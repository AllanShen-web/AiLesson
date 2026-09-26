"""菜单服务：侧边栏数据源，也是角色授权时勾选的那棵树。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.core.db import SessionDep
from ailan.core.errors import BadRequestError, ConflictError
from ailan.menus.models import Menu
from ailan.menus.schemas import MenuCreate, MenuUpdate


class MenuService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[Menu]:
        return list(await self.session.scalars(select(Menu).order_by(Menu.sort, Menu.id)))

    async def find(self, menu_id: int) -> Menu | None:
        return await self.session.get(Menu, menu_id)

    async def find_by_paths(self, paths: list[str]) -> list[Menu]:
        """按授权的路径集合取菜单，保持 sort 顺序；库里已删掉的路径自然过滤掉。"""
        if not paths:
            return []
        granted = set(paths)
        return [menu for menu in await self.list_all() if menu.path in granted]

    async def create(self, data: MenuCreate) -> Menu:
        if await self._find_by_path(data.path):
            raise ConflictError("这个菜单路径已存在")
        if data.parent_id is not None and await self.find(data.parent_id) is None:
            raise BadRequestError("上级菜单不存在")

        menu = Menu(**data.model_dump())
        self.session.add(menu)
        await self.session.flush()
        return menu

    async def update(self, menu_id: int, data: MenuUpdate) -> Menu | None:
        menu = await self.find(menu_id)
        if menu is None:
            return None
        fields = data.model_dump(exclude_unset=True)

        path = fields.get("path")
        if path and path != menu.path and await self._find_by_path(path):
            raise ConflictError("这个菜单路径已存在")
        await self._assert_parent_ok(menu_id, fields.get("parent_id", menu.parent_id))

        for field, value in fields.items():
            setattr(menu, field, value)
        await self.session.flush()
        return menu

    async def remove(self, menu_id: int) -> None:
        menu = await self.find(menu_id)
        if menu is not None:
            # 子菜单随 ORM 关系级联删掉；角色那边残留的 path 会在授权时被过滤，不会报错
            await self.session.delete(menu)

    async def _find_by_path(self, path: str) -> Menu | None:
        return await self.session.scalar(select(Menu).where(Menu.path == path))

    async def _assert_parent_ok(self, menu_id: int, parent_id: int | None) -> None:
        """挂到自己或自己的后代下面会让侧边栏递归渲染时死循环，所以在写入前挡住。"""
        if parent_id is None:
            return
        if parent_id == menu_id:
            raise BadRequestError("上级菜单不能是自己")

        parent = await self.session.get(Menu, parent_id)
        if parent is None:
            raise BadRequestError("上级菜单不存在")

        # 沿上级链往上走：走到自己，说明要挂的这个本来就是自己的后代
        ancestor_id = parent.parent_id
        hops = 0
        while ancestor_id is not None and hops < 20:
            if ancestor_id == menu_id:
                raise BadRequestError("上级菜单不能是自己的子菜单")
            ancestor = await self.session.get(Menu, ancestor_id)
            ancestor_id = ancestor.parent_id if ancestor else None
            hops += 1


def get_menu_service(session: SessionDep) -> MenuService:
    return MenuService(session)


MenuServiceDep = Annotated[MenuService, Depends(get_menu_service)]
