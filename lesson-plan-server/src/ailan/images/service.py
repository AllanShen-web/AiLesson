"""图片服务：所有图片（头像、祝福、留言本、资料封面）共用这一个落库入口。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.core.config import settings
from ailan.core.db import SessionDep
from ailan.core.errors import NotFoundError
from ailan.images.models import Image
from ailan.images.schemas import ImageRef
from ailan.storage.service import StorageDep, StorageService

logger = logging.getLogger(__name__)


def image_url(image_id: int) -> str:
    """访问地址的固定形状。

    库里存的历史链接都是这个形状，改了就等于把已经发出去的地址全断掉，
    所以前缀取配置、不写死，但结构不动。
    """
    return f"/{settings.api_prefix}/images/{image_id}"


class ImageService:
    def __init__(self, session: AsyncSession, storage: StorageService) -> None:
        self.session = session
        self.storage = storage

    async def save(self, filename: str, mimetype: str, data: bytes) -> ImageRef:
        key = self.storage.new_key("image", mimetype, filename)
        await self.storage.put(key, data, mimetype)
        image = Image(filename=filename, mimetype=mimetype, storage_key=key)
        self.session.add(image)
        # 取自增 id 用，真正的提交在请求收尾时统一做
        await self.session.flush()
        logger.info("图片上传成功 id=%s key=%s 大小=%s", image.id, key, len(data))
        return ImageRef(id=image.id, url=image_url(image.id))

    async def find(self, image_id: int) -> Image | None:
        return await self.session.scalar(select(Image).where(Image.id == image_id))

    async def get(self, image_id: int) -> Image:
        image = await self.find(image_id)
        if image is None:
            raise NotFoundError("图片不存在")
        return image

    async def open_source(self, image: Image) -> AsyncIterator[bytes]:
        """出图：新数据是对象存储的流，历史 bytea 整份读出。"""
        if image.storage_key:
            return await self.storage.open_stream(image.storage_key)
        if image.data:
            return _one_shot(image.data)
        raise NotFoundError("图片内容不存在")

    async def delete(self, image_id: int) -> None:
        image = await self.find(image_id)
        if image is None:
            return
        await self.session.delete(image)
        # 先删库再删对象：反过来一旦对象删失败，库里就留了一条永远取不到内容的记录
        if image.storage_key:
            await self.storage.remove(image.storage_key)


async def _one_shot(data: bytes) -> AsyncIterator[bytes]:
    yield data


def get_image_service(session: SessionDep, storage: StorageDep) -> ImageService:
    return ImageService(session, storage)


ImageServiceDep = Annotated[ImageService, Depends(get_image_service)]
