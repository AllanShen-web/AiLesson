"""认证路由：注册、两种登录、验证码下发。

这一组是整站唯一的公开写接口，所以校验全在 Pydantic 层做严：
用户名长度、邮箱格式、密码最小长度不合规，根本进不到服务层。
"""

from __future__ import annotations

from fastapi import APIRouter

from ailan.auth.schemas import (
    EmailInput,
    LoginByEmailInput,
    LoginInput,
    LoginResult,
    RegisterInput,
    SendCodeInput,
    UsernameCheckResult,
    UsernameInput,
)
from ailan.auth.service import AuthServiceDep
from ailan.core.envelope import EnvelopeRoute
from ailan.users.schemas import Notice, UserView

router = APIRouter(prefix="/auth", tags=["认证"], route_class=EnvelopeRoute)


@router.post("/register", response_model=UserView, summary="注册")
async def register(payload: RegisterInput, auth: AuthServiceDep) -> UserView:
    user = await auth.register(payload)
    return UserView.model_validate(user)


@router.post("/check-username", response_model=UsernameCheckResult, summary="用户名是否已被占用")
async def check_username(payload: UsernameInput, auth: AuthServiceDep) -> UsernameCheckResult:
    return await auth.check_username(payload.username)


@router.post("/login", response_model=LoginResult, summary="用户名密码登录")
async def login(payload: LoginInput, auth: AuthServiceDep) -> LoginResult:
    return await auth.login(payload.username, payload.password)


@router.post("/send-code", response_model=Notice, summary="发送注册验证码")
async def send_code(payload: SendCodeInput, auth: AuthServiceDep) -> Notice:
    return Notice(message=await auth.send_code(payload.email, payload.purpose))


@router.post("/send-login-code", response_model=Notice, summary="发送登录验证码")
async def send_login_code(payload: EmailInput, auth: AuthServiceDep) -> Notice:
    return Notice(message=await auth.send_login_code(payload.email))


@router.post("/login-by-email", response_model=LoginResult, summary="邮箱验证码登录")
async def login_by_email(payload: LoginByEmailInput, auth: AuthServiceDep) -> LoginResult:
    return await auth.login_by_email(payload.email, payload.code)
