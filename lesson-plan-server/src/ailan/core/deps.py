"""接口层的依赖：登录身份、权限码、资源归属。

路由上要保护某条接口，就是在它身上声明依赖，不写在函数体里：

    @router.put("/config", dependencies=[Depends(require_permission("ai_config:update"))])
    async def update_config(payload: ConfigInput, user: CurrentUserDep, ...): ...

没声明依赖的接口，任何登录用户都能调——权限只有一个来源，就是这一处声明。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from ailan.core.db import SessionDep
from ailan.core.errors import ForbiddenError, UnauthorizedError
from ailan.core.permissions import is_super_admin, permission_codes_for_menu_paths
from ailan.core.security import decode_access_token
from ailan.roles.models import Role
from ailan.users.models import User

# auto_error=False：缺令牌时自己抛业务异常，才能回出统一的 `{code, message}` 而不是 FastAPI 默认结构
bearer_scheme = HTTPBearer(auto_error=False, description="登录接口下发的 JWT")


@dataclass(frozen=True)
class CurrentUser:
    """从令牌还原出的登录身份。权限码不在这里，每次都按角色当前授权实时算。"""

    id: int
    username: str
    nickname: str | None
    role_code: str | None


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: SessionDep,
) -> CurrentUser:
    if credentials is None:
        raise UnauthorizedError("请先登录")

    payload = decode_access_token(credentials.credentials)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthorizedError("登录凭证无效") from exc

    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise UnauthorizedError("账号不存在或已注销")
    if user.frozen:
        raise ForbiddenError("账户已被冻结")

    return CurrentUser(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        role_code=user.role_code,
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_permission(code: str) -> Callable[..., object]:
    """按权限码拦截：用户 → role_code → 角色已授权的菜单 → 这些菜单携带的权限码。"""

    async def dependency(user: CurrentUserDep, session: SessionDep) -> CurrentUser:
        if is_super_admin(user.role_code):
            return user
        if not user.role_code:
            raise ForbiddenError("账号未分配角色，无权访问")

        role = await session.scalar(select(Role).where(Role.code == user.role_code))
        granted = permission_codes_for_menu_paths(role.menu_paths if role else [])
        if code not in granted:
            raise ForbiddenError(f"缺少权限: {code}")
        return user

    return dependency


__all__ = [
    "CurrentUser",
    "CurrentUserDep",
    "SessionDep",
    "get_current_user",
    "require_permission",
]
