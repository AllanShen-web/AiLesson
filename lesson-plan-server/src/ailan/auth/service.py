"""认证服务：注册、两种登录、验证码下发与频控。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import Depends
from starlette.concurrency import run_in_threadpool

from ailan.auth.schemas import LoginResult, RegisterInput, UsernameCheckResult
from ailan.blessings.service import BlessingSettingService, BlessingSettingServiceDep
from ailan.core.errors import BadRequestError, ConflictError, ForbiddenError, UnauthorizedError
from ailan.core.security import create_access_token, verify_password
from ailan.mail.service import MailDep, MailService
from ailan.roles.schemas import RoleView
from ailan.roles.service import RoleService, RoleServiceDep
from ailan.users.models import User
from ailan.users.schemas import UserProfile
from ailan.users.service import UserService, UserServiceDep

REGISTER_PURPOSE = "注册验证"
LOGIN_PURPOSE = "登录验证"

COOLDOWN_SECONDS = 60
DAILY_LIMIT = 10


@dataclass
class _SendRecord:
    last_sent: float
    count: int
    day: date


class EmailRateLimiter:
    """按收件邮箱限频：60 秒冷却 + 每日上限。

    按邮箱而不是按来源 IP：同一办公网的请求出来是同一个出口 IP，
    按 IP 限等于第 11 个用户就再也收不到验证码。

    只存进程内存，重启清零——这一层挡的是"手滑连点"和小额滥用，
    真要挡住脚本得在网关层做，不该让应用背这个责任。
    """

    def __init__(self, cooldown_seconds: int = COOLDOWN_SECONDS, daily_limit: int = DAILY_LIMIT) -> None:
        self.cooldown_seconds = cooldown_seconds
        self.daily_limit = daily_limit
        self._records: dict[str, _SendRecord] = {}

    def assert_allowed(self, email: str) -> None:
        record = self._records.get(email)
        if record is None or record.day != date.today():
            return

        elapsed = time.monotonic() - record.last_sent
        # elapsed >= 0 这个判断不能省：单调时钟一般不倒退，但真倒退时负数会算出
        # "请 28056 秒后再试"这种离谱提示，宁可不拦也别乱拦
        if record.last_sent > 0 and elapsed >= 0 and elapsed < self.cooldown_seconds:
            remain = round(self.cooldown_seconds - elapsed) or 1
            raise BadRequestError(f"发送过于频繁，请 {remain} 秒后再试")
        if record.count >= self.daily_limit:
            raise BadRequestError("今日发送次数已达上限，请明天再试")

    def mark_sent(self, email: str) -> None:
        today = date.today()
        self._prune(today)
        previous = self._records.get(email)
        counted = previous.count + 1 if previous and previous.day == today else 1
        self._records[email] = _SendRecord(last_sent=time.monotonic(), count=counted, day=today)

    def _prune(self, today: date) -> None:
        """跨天的记录一律丢掉，这张表不该只增不减。"""
        for email in [email for email, record in self._records.items() if record.day != today]:
            self._records.pop(email, None)


class AuthService:
    def __init__(
        self,
        users: UserService,
        roles: RoleService,
        mail: MailService,
        blessings: BlessingSettingService,
        limiter: EmailRateLimiter,
    ) -> None:
        self.users = users
        self.roles = roles
        self.mail = mail
        self.blessings = blessings
        self.limiter = limiter

    async def check_username(self, username: str) -> UsernameCheckResult:
        # 只查不建：注册页要在用户敲完用户名时就报"已被占用"，不必等到提交
        exists = await self.users.find_by_username(username) is not None
        return UsernameCheckResult(exists=exists)

    async def send_code(self, email: str, purpose: str) -> str:
        self.limiter.assert_allowed(email)
        if await self.users.find_by_email(email):
            raise ConflictError("该邮箱已被注册")
        await self.mail.send_code(email, purpose)
        # 先确认发出去了再扣额度：否则邮件发送失败也把用户当天的额度吃掉了
        self.limiter.mark_sent(email)
        return "验证码已发送"

    async def send_login_code(self, email: str) -> str:
        self.limiter.assert_allowed(email)
        if await self.users.find_by_email(email) is None:
            raise BadRequestError("该邮箱未注册")
        await self.mail.send_code(email, LOGIN_PURPOSE)
        self.limiter.mark_sent(email)
        return "验证码已发送"

    async def register(self, payload: RegisterInput) -> User:
        if not self.mail.verify_code(payload.email, payload.code):
            raise BadRequestError("验证码无效或已过期")

        user = await self.users.create(
            username=payload.username,
            password=payload.password,
            email=payload.email,
            nickname=payload.nickname,
        )
        # 新用户第一天就该有一条祝福开关配置，否则留言本要点开开关页才建
        await self.blessings.ensure_for_user(user.id, payload.nickname or payload.username)
        return user

    async def login(self, username: str, password: str) -> LoginResult:
        if not username or not password:
            raise BadRequestError("请输入用户名和密码")
        user = await self.users.find_by_username(username)
        # 账号不存在和密码错回同一句话：否则这张表能被拿来枚举哪些用户名注册过
        if user is None or not await _verify(password, user.password):
            raise UnauthorizedError("用户名或密码错误")
        return await self._login_response(user)

    async def login_by_email(self, email: str, code: str) -> LoginResult:
        if not self.mail.verify_code(email, code):
            raise BadRequestError("验证码无效或已过期")
        user = await self.users.find_by_email(email)
        if user is None:
            raise UnauthorizedError("该邮箱未注册")
        return await self._login_response(user)

    async def _login_response(self, user: User) -> LoginResult:
        if user.frozen:
            raise ForbiddenError("该账户已被冻结")

        token = create_access_token({"sub": str(user.id), "username": user.username})
        role = await self.roles.find_by_code(user.role_code)
        permissions = await self.roles.permission_codes_for(user.role_code)
        return LoginResult(
            token=token,
            user_info=UserProfile(
                id=user.id,
                username=user.username,
                nickname=user.nickname,
                email=user.email,
                role_code=user.role_code,
                frozen=user.frozen,
                avatar=user.avatar,
                created_at=user.created_at,
                role=RoleView.model_validate(role) if role else None,
                permissions=permissions,
            ),
        )


async def _verify(raw: str, hashed: str) -> bool:
    return await run_in_threadpool(verify_password, raw, hashed)


email_rate_limiter = EmailRateLimiter()


def get_email_rate_limiter() -> EmailRateLimiter:
    return email_rate_limiter


EmailRateLimiterDep = Annotated[EmailRateLimiter, Depends(get_email_rate_limiter)]


def get_auth_service(
    users: UserServiceDep,
    roles: RoleServiceDep,
    mail: MailDep,
    blessings: BlessingSettingServiceDep,
    limiter: EmailRateLimiterDep,
) -> AuthService:
    return AuthService(users, roles, mail, blessings, limiter)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
