"""角色路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ailan.core.deps import get_current_user, require_permission
from ailan.core.envelope import EnvelopeRoute
from ailan.core.errors import NotFoundError
from ailan.core.permissions import PERMISSION_CATALOG
from ailan.roles.models import Role
from ailan.roles.schemas import (
    MenuGrantInput,
    MenuGrantResult,
    PermissionCatalogEntry,
    RoleCreate,
    RoleUpdate,
    RoleView,
)
from ailan.roles.service import RoleServiceDep

# 角色接口一律要登录；哪些还要权限码，逐条写在路由的 dependencies 上
router = APIRouter(
    prefix="/roles",
    tags=["角色"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/permission-catalog",
    response_model=list[PermissionCatalogEntry],
    dependencies=[Depends(require_permission("system:role_manage"))],
    summary="受保护操作目录",
)
async def permission_catalog() -> list[PermissionCatalogEntry]:
    """只读：给菜单授权界面展示"这个菜单会带来哪些操作"，不落库、不可勾选。"""
    return [
        PermissionCatalogEntry(code=item.code, label=item.label, menu_path=item.menu_path)
        for item in PERMISSION_CATALOG
    ]


@router.get("", response_model=list[RoleView], summary="角色列表")
async def list_roles(roles: RoleServiceDep) -> list[Role]:
    """登录即可读：用户管理页的角色下拉也要用它。"""
    return await roles.list_all()


@router.post(
    "",
    response_model=RoleView,
    status_code=201,
    dependencies=[Depends(require_permission("system:role_manage"))],
    summary="新建角色",
)
async def create_role(payload: RoleCreate, roles: RoleServiceDep) -> Role:
    return await roles.create(payload)


@router.get(
    "/{role_id}/menus",
    response_model=list[str],
    dependencies=[Depends(require_permission("system:role_manage"))],
    summary="已授权的菜单",
)
async def get_menus(role_id: int, roles: RoleServiceDep) -> list[str]:
    return await roles.menu_paths_of(role_id)


@router.put(
    "/{role_id}/menus",
    response_model=MenuGrantResult,
    dependencies=[Depends(require_permission("system:role_manage"))],
    summary="菜单授权",
)
async def set_menus(role_id: int, payload: MenuGrantInput, roles: RoleServiceDep) -> MenuGrantResult:
    """菜单授权 = 权限授权：勾中的菜单，其携带的操作码同时生效。"""
    paths = await roles.set_menu_paths(role_id, payload.menu_paths)
    return MenuGrantResult(role_id=role_id, menu_paths=paths)


@router.put(
    "/{role_id}",
    response_model=RoleView,
    dependencies=[Depends(require_permission("system:role_manage"))],
    summary="修改角色",
)
async def update_role(role_id: int, payload: RoleUpdate, roles: RoleServiceDep) -> Role:
    role = await roles.update(role_id, payload)
    if role is None:
        raise NotFoundError("角色不存在")
    return role


@router.delete(
    "/{role_id}",
    dependencies=[Depends(require_permission("system:role_manage"))],
    summary="删除角色",
)
async def remove_role(role_id: int, roles: RoleServiceDep) -> dict[str, int]:
    await roles.remove(role_id)
    return {"id": role_id}
