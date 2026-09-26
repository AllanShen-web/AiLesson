"""菜单路由：本人可见的菜单，和管理员的菜单维护。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ailan.core.deps import CurrentUserDep, require_permission
from ailan.core.envelope import EnvelopeRoute
from ailan.core.errors import NotFoundError
from ailan.core.permissions import is_super_admin
from ailan.menus.models import Menu
from ailan.menus.schemas import MenuCreate, MenuUpdate, MenuView
from ailan.menus.service import MenuServiceDep
from ailan.roles.service import RoleServiceDep

# 菜单接口一律要登录；写操作额外要 system:menu_manage
router = APIRouter(
    prefix="/menus",
    tags=["菜单"],
    route_class=EnvelopeRoute,
)


@router.get("", response_model=list[MenuView], summary="全部菜单")
async def list_menus(menus: MenuServiceDep) -> list[Menu]:
    """登录即可读：角色管理的授权弹窗也要拿这份数据画树。"""
    return await menus.list_all()


@router.get("/mine", response_model=list[MenuView], summary="当前用户可见菜单")
async def my_menus(user: CurrentUserDep, menus: MenuServiceDep, roles: RoleServiceDep) -> list[MenuView]:
    """侧边栏就是这份数据：超级角色给全部，其余按角色勾过的菜单路径过滤。"""
    if is_super_admin(user.role_code):
        return [MenuView.of(menu) for menu in await menus.list_all()]

    role = await roles.find_by_code(user.role_code)
    if role is None:
        return []
    return [MenuView.of(menu) for menu in await menus.find_by_paths(role.menu_paths)]


@router.post(
    "",
    response_model=MenuView,
    status_code=201,
    dependencies=[Depends(require_permission("system:menu_manage"))],
    summary="新建菜单",
)
async def create_menu(payload: MenuCreate, menus: MenuServiceDep) -> Menu:
    return await menus.create(payload)


@router.put(
    "/{menu_id}",
    response_model=MenuView,
    dependencies=[Depends(require_permission("system:menu_manage"))],
    summary="修改菜单",
)
async def update_menu(menu_id: int, payload: MenuUpdate, menus: MenuServiceDep) -> Menu:
    menu = await menus.update(menu_id, payload)
    if menu is None:
        raise NotFoundError("菜单不存在")
    return menu


@router.delete(
    "/{menu_id}",
    dependencies=[Depends(require_permission("system:menu_manage"))],
    summary="删除菜单",
)
async def remove_menu(menu_id: int, menus: MenuServiceDep) -> dict[str, int]:
    menu = await menus.find(menu_id)
    if menu is None:
        raise NotFoundError("菜单不存在")
    await menus.remove(menu_id)
    return {"id": menu_id}
