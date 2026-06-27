"""用户接口的数据形状。"""

from __future__ import annotations

from datetime import datetime

from pydantic import EmailStr, Field

from ailan.core.schemas import ApiModel
from ailan.roles.schemas import RoleView

USERNAME_MIN = 2
USERNAME_MAX = 20
PASSWORD_MIN = 6


class UserView(ApiModel):
    """对外可见的用户信息：`password` 从来不在这里，也就不会被顺手带出去。"""

    id: int
    username: str
    nickname: str | None = None
    email: str | None = None
    role_code: str | None = None
    frozen: bool
    avatar: str | None = None
    created_at: datetime


class UserWithRole(UserView):
    """用户列表里的一行：带上角色，前端不用再为每行发一次请求。"""

    role: RoleView | None = None


class UserProfile(UserView):
# TODO 前端要做按钮级判断，权限码得跟账号信息一起返回
    """`GET /user/me`：账号信息 + 角色 + 权限码。

    权限码是给前端做页面和按钮级判断用的；真正的放行与否在接口上的权限依赖里，
    前端藏了按钮不等于调不到。
    """

    role: RoleView | None = None
    permissions: list[str] = Field(default_factory=list)


class ProfileUpdate(ApiModel):
    # 用户名要查重，改自己名字时和注册走同一套校验
    username: str | None = Field(default=None, min_length=USERNAME_MIN, max_length=USERNAME_MAX)
    nickname: str | None = Field(default=None, max_length=20)


class AdminUserUpdate(ApiModel):
    """管理员改账号：可以多改邮箱和角色，但不能改密码（改密码只走本人流程）。"""

    username: str | None = Field(default=None, min_length=USERNAME_MIN, max_length=USERNAME_MAX)
    email: EmailStr | None = None
    nickname: str | None = Field(default=None, max_length=20)
    role_code: str | None = None


class AvatarSelectInput(ApiModel):
    avatar_url: str


class PasswordChangeInput(ApiModel):
    code: str
    new_password: str = Field(min_length=PASSWORD_MIN)


class NewEmailInput(ApiModel):
    new_email: EmailStr


class EmailChangeInput(ApiModel):
    new_email: EmailStr
    # 换邮箱要验原密码：只靠新邮箱的验证码，等于谁拿到别人邮箱就能接管账号
    password: str
    code: str


class Notice(ApiModel):
    message: str
