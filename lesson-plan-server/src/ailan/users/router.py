"""用户路由：本人信息与管理员的用户管理。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, UploadFile

from ailan.core.deps import CurrentUserDep, require_permission
from ailan.core.envelope import EnvelopeRoute
from ailan.core.errors import BadRequestError, NotFoundError, TooLargeError
from ailan.images.service import ImageServiceDep
from ailan.roles.schemas import RoleView
from ailan.roles.service import RoleServiceDep
from ailan.users.models import User
from ailan.users.schemas import (
    AdminUserUpdate,
    AvatarSelectInput,
    EmailChangeInput,
    NewEmailInput,
    Notice,
    PasswordChangeInput,
    ProfileUpdate,
    UserProfile,
    UserView,
    UserWithRole,
)
from ailan.users.service import UserServiceDep

router = APIRouter(prefix="/users", tags=["用户"], route_class=EnvelopeRoute)

AVATAR_MIMETYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
AVATAR_MAX_BYTES = 10 * 1024 * 1024

# 注意顺序：`/me/...` 必须在 `/{user_id}/...` 之前声明。
# 路由按注册顺序匹配，先匹配到 `/{user_id}` 会把 "me" 当成 id 去转 int，报 422 而不是走到本人接口。


@router.get("/me", response_model=UserProfile, summary="当前登录用户")
async def me(user: CurrentUserDep, users: UserServiceDep, roles: RoleServiceDep) -> UserProfile:
    account = await users.find(user.id)
    if account is None:
        raise NotFoundError("账号不存在或已注销")
    role = await roles.find_by_code(account.role_code)
    permissions = await roles.permission_codes_for(account.role_code)
    return UserProfile(
        **_view_fields(account), role=RoleView.model_validate(role) if role else None, permissions=permissions
    )


@router.put("/me/profile", response_model=UserView, summary="修改本人资料")
async def update_profile(payload: ProfileUpdate, user: CurrentUserDep, users: UserServiceDep) -> User:
    account = await users.update(user.id, payload.model_dump(exclude_unset=True))
    if account is None:
        raise NotFoundError("账号不存在或已注销")
    return account


@router.post("/me/avatar", summary="上传头像")
async def upload_avatar(
    file: UploadFile, user: CurrentUserDep, users: UserServiceDep, images: ImageServiceDep
) -> dict[str, str]:
    data = await file.read()
    if file.content_type not in AVATAR_MIMETYPES:
        raise BadRequestError("只允许上传 jpg/png/gif/webp 格式图片")
    if len(data) > AVATAR_MAX_BYTES:
        raise TooLargeError("头像不能超过 10MB")

    saved = await images.save(file.filename or "", file.content_type or "image/png", data)
    await users.set_avatar(user.id, saved.url)
    return {"url": saved.url}


@router.get("/me/avatar-history", response_model=list[str], summary="头像历史")
async def avatar_history(user: CurrentUserDep, users: UserServiceDep) -> list[str]:
    return await users.avatar_history(user.id)


@router.put("/me/avatar-history", response_model=UserView, summary="用回历史里的某张头像")
async def use_avatar_from_history(
    payload: AvatarSelectInput, user: CurrentUserDep, users: UserServiceDep
) -> User:
    account = await users.use_avatar_from_history(user.id, payload.avatar_url)
    if account is None:
        raise NotFoundError("账号不存在或已注销")
    return account


@router.delete("/me/avatar-history/{index}", response_model=list[str], summary="删掉历史里的某张头像")
async def drop_avatar_from_history(index: int, user: CurrentUserDep, users: UserServiceDep) -> list[str]:
    history = await users.drop_avatar_from_history(user.id, index)
    if history is None:
        raise BadRequestError("没有这个历史头像")
    return history


@router.post("/me/change-password/code", response_model=Notice, summary="发改密码验证码")
async def send_change_password_code(user: CurrentUserDep, users: UserServiceDep) -> Notice:
    return Notice(message=await users.send_change_password_code(user.id))


@router.put("/me/password", response_model=Notice, summary="用验证码改密码")
async def change_password(
    payload: PasswordChangeInput, user: CurrentUserDep, users: UserServiceDep
) -> Notice:
    message = await users.change_password_by_code(user.id, payload.code, payload.new_password)
    return Notice(message=message)


@router.post("/me/email/code", response_model=Notice, summary="给新邮箱发验证码")
async def send_new_email_code(payload: NewEmailInput, users: UserServiceDep) -> Notice:
    return Notice(message=await users.send_new_email_code(payload.new_email))


@router.put("/me/email", response_model=UserView, summary="换邮箱")
async def change_email(payload: EmailChangeInput, user: CurrentUserDep, users: UserServiceDep) -> User:
    account = await users.change_email(user.id, payload.new_email, payload.password, payload.code)
    if account is None:
        raise NotFoundError("账号不存在或已注销")
    return account


@router.get(
    "",
    response_model=list[UserWithRole],
    dependencies=[Depends(require_permission("system:user_manage"))],
    summary="用户列表",
)
async def list_users(users: UserServiceDep, roles: RoleServiceDep) -> list[UserWithRole]:
    accounts = await users.list_all()
    # 角色一次取全：逐行查角色会在用户数上去之后变成 N+1 条 SQL
    by_code = {role.code: role for role in await roles.list_all()}
    return [
        UserWithRole(
            **_view_fields(account),
            role=RoleView.model_validate(by_code[account.role_code])
            if account.role_code in by_code
            else None,
        )
        for account in accounts
    ]


@router.put(
    "/{user_id}",
    response_model=UserView,
    dependencies=[Depends(require_permission("system:user_manage"))],
    summary="修改账号",
)
async def update_user(user_id: int, payload: AdminUserUpdate, users: UserServiceDep) -> User:
    account = await users.update(user_id, payload.model_dump(exclude_unset=True))
    if account is None:
        raise NotFoundError("用户不存在")
    return account


@router.put(
    "/{user_id}/frozen",
    response_model=UserView,
    dependencies=[Depends(require_permission("system:user_manage"))],
    summary="冻结 / 解冻账号",
)
async def toggle_frozen(user_id: int, users: UserServiceDep) -> User:
    account = await users.toggle_frozen(user_id)
    if account is None:
        raise NotFoundError("用户不存在")
    return account


@router.delete(
    "/{user_id}",
    dependencies=[Depends(require_permission("system:user_manage"))],
    summary="删除账号",
)
async def remove_user(user_id: int, users: UserServiceDep) -> dict[str, int]:
    await users.remove(user_id)
    return {"id": user_id}


def _view_fields(account: User) -> dict[str, Any]:
    """列表和详情共用同一套字段：直接从 `UserView` 倒出来，字段与类型都跟着出参模型走，
    两边不会漂，也不会漏掉新加的列。
    """
    return UserView.model_validate(account).model_dump()
