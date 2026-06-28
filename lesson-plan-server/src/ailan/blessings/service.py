"""祝福墙服务：留言的读写与每个人的墙面配置。

留言是公开功能（不登录也能写），配置是私人功能（只有本人能改）。
两件事放一个文件是因为它们共用这两张表，也共用"这条留言给谁看"的判断。
"""

from __future__ import annotations

import re
from random import choice
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.blessings.models import Blessing, BlessingSetting
from ailan.blessings.schemas import BlessingCreate, default_settings
from ailan.core.db import SessionDep
from ailan.core.errors import NotFoundError
from ailan.images.service import ImageService, ImageServiceDep
from ailan.users.models import User

ANONYMOUS_USER_ID = 0

# 气泡底色随机给，界面上不用选色；最后一屏五颜六色的效果比全是同一个粉要热闹
BUBBLE_COLORS: tuple[str, ...] = (
    "#fd79a8",
    "#6c5ce7",
    "#00b894",
    "#fdcb6e",
    "#e17055",
    "#0984e3",
    "#00cec9",
    "#a29bfe",
    "#fab1a0",
    "#74b9ff",
)

# 图片地址形如 `/api/images/12`：留言里存的是地址而不是对象 key，删图要先把 id 抠回来
_IMAGE_ID_IN_URL = re.compile(r"/images/(\d+)")


class BlessingService:
    def __init__(self, session: AsyncSession, images: ImageService) -> None:
        self.session = session
        self.images = images

    async def list_for_user(self, user_id: int, username: str) -> list[Blessing]:
        """我收到的 + 我发出的，含已被下架的那些：自己的墙要能看见隐藏状态才好取消。"""
        return list(
            await self.session.scalars(
                select(Blessing)
                .where(or_(Blessing.user_id == user_id, Blessing.target_username == username))
                .order_by(Blessing.created_at.desc())
            )
        )

    async def list_visible_by_username(self, username: str) -> list[Blessing]:
        """某人的公开祝福墙：挂在他名下的、写给他本人的，且不显示已下架的。

        账号不存在时按"没人"处理返回空列表——这是一面墙，不存在比 404 更符合用户预期。
        """
        owner_id = await self.session.scalar(select(User.id).where(User.username == username)) or 0
        return list(
            await self.session.scalars(
                select(Blessing)
                .where(
                    or_(Blessing.user_id == owner_id, Blessing.target_username == username),
                    Blessing.hidden.is_(False),
                )
                .order_by(Blessing.created_at.desc())
            )
        )

    async def create(self, data: BlessingCreate) -> Blessing:
        blessing = Blessing(
            user_id=ANONYMOUS_USER_ID,
            nickname=data.nickname,
            content=data.content,
            image_url=data.image_url,
            color=choice(BUBBLE_COLORS),
            temp_id=data.temp_id,
            target_username=data.target_username,
        )
        self.session.add(blessing)
        await self.session.flush()
        return blessing

    async def toggle_hidden(self, blessing_id: int, user_id: int) -> Blessing:
        blessing = await self.session.scalar(
            select(Blessing).where(Blessing.id == blessing_id, Blessing.user_id == user_id)
        )
        if blessing is None:
            raise NotFoundError("留言不存在或无权操作")
        blessing.hidden = not blessing.hidden
        await self.session.flush()
        return blessing

    async def remove(self, blessing_id: int, user_id: int) -> dict[str, int]:
        """本人下架自己的留言，配图一并删。"""
        blessing = await self.session.scalar(
            select(Blessing).where(Blessing.id == blessing_id, Blessing.user_id == user_id)
        )
        if blessing is None:
            return {"id": blessing_id}
        await self._delete_image(blessing)
        await self.session.delete(blessing)
        return {"id": blessing_id}

    async def remove_by_temp_id(self, blessing_id: int, temp_id: str) -> dict[str, int]:
        """匿名撤回：没登录，凭发送时自己生成的 tempId 认领。

        对不上就当这条不存在——回"tempId 不对"等于告诉别人这个 id 下有留言。
        """
        blessing = await self.session.get(Blessing, blessing_id)
        if blessing is None or blessing.temp_id != temp_id:
            raise NotFoundError("留言不存在")
        await self._delete_image(blessing)
        await self.session.delete(blessing)
        return {"id": blessing_id}

    async def _delete_image(self, blessing: Blessing) -> None:
        if not blessing.image_url:
            return
        match = _IMAGE_ID_IN_URL.search(blessing.image_url)
        if match:
            await self.images.delete(int(match.group(1)))


class BlessingSettingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ensure_for_user(self, user_id: int, display_name: str = "") -> BlessingSetting:
        """没有就按昵称建一份默认配置，有就原样返回。

        注册时调用；老用户可能没有这一行（这功能上线前建的号），所以读取入口也要走它。
        """
        setting = await self.find(user_id)
        if setting is not None:
            return setting
        setting = BlessingSetting(user_id=user_id, settings=default_settings(display_name), enabled=True)
        self.session.add(setting)
        await self.session.flush()
        return setting

    async def find(self, user_id: int) -> BlessingSetting | None:
        return await self.session.scalar(select(BlessingSetting).where(BlessingSetting.user_id == user_id))

    async def find_by_username(self, username: str) -> tuple[BlessingSetting, User] | None:
        """公开读别人主页时要连账号一起返回：墙面上要写主人的用户名和昵称。"""
        user = await self.session.scalar(select(User).where(User.username == username))
        if user is None:
            return None
        setting = await self.find(user.id)
        if setting is None:
            return None
        return setting, user

    async def update(self, user_id: int, settings: dict[str, Any]) -> BlessingSetting:
        setting = await self.ensure_for_user(user_id)
        setting.settings = settings
        await self.session.flush()
        return setting

    async def toggle_enabled(self, user_id: int) -> BlessingSetting:
        setting = await self.ensure_for_user(user_id)
        setting.enabled = not setting.enabled
        await self.session.flush()
        return setting


def get_blessing_service(session: SessionDep, images: ImageServiceDep) -> BlessingService:
    return BlessingService(session, images)


BlessingServiceDep = Annotated[BlessingService, Depends(get_blessing_service)]


def get_blessing_setting_service(session: SessionDep) -> BlessingSettingService:
    return BlessingSettingService(session)


BlessingSettingServiceDep = Annotated[BlessingSettingService, Depends(get_blessing_setting_service)]
