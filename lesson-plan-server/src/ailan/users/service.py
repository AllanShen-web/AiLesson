"""用户服务：账号信息、头像历史、改密码与换邮箱。"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ailan.core.db import SessionDep
from ailan.core.errors import BadRequestError, ConflictError
from ailan.core.security import hash_password, verify_password
from ailan.images.service import ImageServiceDep
from ailan.mail.service import MailDep, MailService
from ailan.users.models import User

# 头像地址的历史形状：/api/images/123
_IMAGE_ID_IN_URL = re.compile(r"/api/images/(\d+)")

CHANGE_PASSWORD_PURPOSE = "修改密码"
EMAIL_CHANGE_PURPOSE = "邮箱变更"

# 注册进来的账号一律是这个角色，权限靠后台「角色管理」授予，不在建号时代码里给
DEFAULT_ROLE_CODE = "user"


class UserService:
    def __init__(self, session: AsyncSession, mail: MailService, images: Any) -> None:
        self.session = session
        self.mail = mail
        self.images = images

    async def find(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def find_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email))

    async def find_by_username(self, username: str) -> User | None:
        return await self.session.scalar(select(User).where(User.username == username))

    async def list_all(self) -> list[User]:
        return list(await self.session.scalars(select(User).order_by(User.id)))

    async def create(self, username: str, password: str, email: str, nickname: str | None) -> User:
        """建号：口令在这里散列，明文一行都不落。"""
        if await self.find_by_username(username):
            raise ConflictError("用户名已存在")
        if await self.find_by_email(email):
            raise ConflictError("该邮箱已被注册")

        hashed = await _hash_in_threadpool(password)
        user = User(
            username=username, password=hashed, email=email, nickname=nickname, role_code=DEFAULT_ROLE_CODE
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def update(self, user_id: int, fields: dict[str, Any]) -> User | None:
        user = await self.find(user_id)
        if user is None:
            return None
        email = fields.get("email")
        if email and await self.find_by_email(email) is not None and email != user.email:
            raise ConflictError("该邮箱已被使用")
        username = fields.get("username")
        if username and username != user.username and await self.find_by_username(username):
            raise ConflictError("用户名已存在")

        for key, value in fields.items():
            if value is not None:
                setattr(user, key, value)
        await self.session.flush()
        return user

    async def toggle_frozen(self, user_id: int) -> User | None:
        user = await self.find(user_id)
        if user is None:
            return None
        user.frozen = not user.frozen
        await self.session.flush()
        return user

    async def remove(self, user_id: int) -> None:
        user = await self.find(user_id)
        if user is not None:
            await self.session.delete(user)

    async def set_avatar(self, user_id: int, avatar_url: str) -> User | None:
        """换头像：旧的那张不删，推进历史列表，用户可以选回上一张。"""
        user = await self.find(user_id)
        if user is None:
            return None
        history = list(user.avatar_history)
        if user.avatar and user.avatar != avatar_url:
            history.append(user.avatar)
        user.avatar = avatar_url
        user.avatar_history = history
        await self.session.flush()
        return user

    async def avatar_history(self, user_id: int) -> list[str]:
        user = await self.find(user_id)
        return list(user.avatar_history) if user else []

    async def use_avatar_from_history(self, user_id: int, avatar_url: str) -> User | None:
        user = await self.find(user_id)
        if user is None:
            return None
        history = [url for url in user.avatar_history if url != avatar_url]
        if user.avatar:
            history.append(user.avatar)
        user.avatar = avatar_url
        user.avatar_history = history
        await self.session.flush()
        return user

    async def drop_avatar_from_history(self, user_id: int, index: int) -> list[str] | None:
        """删掉历史里的某一张：连带把图片本身删掉，不然对象存储里全是取不到的文件。"""
        user = await self.find(user_id)
        if user is None:
            return None
        history = list(user.avatar_history)
        if not 0 <= index < len(history):
            return None
        removed = history.pop(index)
        image_id = _image_id(removed)
        if image_id is not None:
            await self.images.delete(image_id)
        user.avatar_history = history
        await self.session.flush()
        return history

    async def send_change_password_code(self, user_id: int) -> str:
        user = await self.find(user_id)
        if user is None or not user.email:
            raise BadRequestError("请先绑定邮箱")
        await self.mail.send_code(user.email, CHANGE_PASSWORD_PURPOSE)
        return "验证码已发送"

    async def change_password_by_code(self, user_id: int, code: str, new_password: str) -> str:
        user = await self.find(user_id)
        if user is None or not user.email:
            raise BadRequestError("请先绑定邮箱")
        if not self.mail.verify_code(user.email, code):
            raise BadRequestError("验证码无效或已过期")
        user.password = await _hash_in_threadpool(new_password)
        await self.session.flush()
        return "密码修改成功"

    async def send_new_email_code(self, new_email: str) -> str:
        if await self.find_by_email(new_email):
            raise ConflictError("该邮箱已被使用")
        await self.mail.send_code(new_email, EMAIL_CHANGE_PURPOSE)
        return "验证码已发送"

    async def change_email(self, user_id: int, new_email: str, password: str, code: str) -> User | None:
        user = await self.find(user_id)
        if user is None:
            raise BadRequestError("用户不存在")
        # 改邮箱要验原密码，也要验新邮箱的验证码：一个证明是本人，一个证明这个邮箱真能收信
        if not await _verify_in_threadpool(password, user.password):
            raise BadRequestError("密码错误")
        if not self.mail.verify_code(new_email, code):
            raise BadRequestError("验证码无效或已过期")
        if await self.find_by_email(new_email):
            raise ConflictError("该邮箱已被使用")

        user.email = new_email
        await self.session.flush()
        return user


def _image_id(url: str) -> int | None:
    match = _IMAGE_ID_IN_URL.search(url or "")
    return int(match.group(1)) if match else None


async def _hash_in_threadpool(password: str) -> str:
    """bcrypt 是 CPU 密集的，一轮要几百毫秒：放在事件循环里跑，并发请求会一起被卡住。"""
    return await run_in_threadpool(hash_password, password)


async def _verify_in_threadpool(raw: str, hashed: str) -> bool:
    return await run_in_threadpool(verify_password, raw, hashed)


def get_user_service(session: SessionDep, mail: MailDep, images: ImageServiceDep) -> UserService:
    return UserService(session, mail, images)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
