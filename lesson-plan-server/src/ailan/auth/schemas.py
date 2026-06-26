"""认证接口的数据形状。"""

from __future__ import annotations

from pydantic import EmailStr, Field

from ailan.core.schemas import ApiModel
from ailan.users.schemas import USERNAME_MAX, USERNAME_MIN, UserProfile

REGISTER_PASSWORD_MIN = 6


class UsernameInput(ApiModel):
    username: str = Field(min_length=USERNAME_MIN, max_length=USERNAME_MAX)


class UsernameCheckResult(ApiModel):
    exists: bool


class RegisterInput(ApiModel):
    username: str = Field(min_length=USERNAME_MIN, max_length=USERNAME_MAX)
    # 注册密码只要求长度：强制大小写数字符号，用户只会写一张纸贴在键盘边
    password: str = Field(min_length=REGISTER_PASSWORD_MIN)
    email: EmailStr
    code: str
    nickname: str | None = Field(default=None, max_length=20)


class LoginInput(ApiModel):
    username: str
    password: str


class SendCodeInput(ApiModel):
    email: EmailStr
    # 用途会同时出现在邮件标题和正文里，比如"注册验证""登录验证"
    purpose: str = Field(max_length=20)


class EmailInput(ApiModel):
    email: EmailStr


class LoginByEmailInput(ApiModel):
    email: EmailStr
    code: str


class LoginResult(ApiModel):
    """登录成功只给两样东西：令牌，和前端立刻要用的账号信息。"""

    token: str
    user_info: UserProfile
