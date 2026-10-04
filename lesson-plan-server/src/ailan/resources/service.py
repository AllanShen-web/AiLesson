"""资料服务：上传、列表、改名与公开，删除时连带清理向量数据。

这一层只管"资料"这张表；文件字节怎么取（桶里还是历史 bytea）在 `files.py`，
向量化进度在 `rag`。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.core.db import SessionDep
from ailan.core.errors import ForbiddenError, NotFoundError
from ailan.rag.service import RagService, RagServiceDep
from ailan.resources.files import open_source
from ailan.resources.models import Resource
from ailan.resources.schemas import ORIGINAL_NAME_MAX, ResourceUpdate, ResourceView
from ailan.storage.service import StorageDep, StorageService

logger = logging.getLogger(__name__)

# creator_name 这一列只有 20 字符，长用户名必须截而不是让数据库报错
CREATOR_NAME_MAX = 20


class ResourceService:
    def __init__(self, session: AsyncSession, rag: RagService, storage: StorageService) -> None:
        self.session = session
        self.rag = rag
        self.storage = storage

    async def find(self, resource_id: int) -> Resource | None:
        return await self.session.get(Resource, resource_id)

    async def get(self, resource_id: int) -> Resource:
        resource = await self.session.get(Resource, resource_id)
        if resource is None:
            raise NotFoundError("资料库不存在")
        return resource

    async def find_visible(self, resource_id: int, user_id: int) -> Resource:
        """详情、预览、下载共用的口径：本人上传的或已公开的。

        这里放行公开资料，编辑与删除走的是 `_owned_by`——只看得到不等于改得动。
        """
        resource = await self.get(resource_id)
        if resource.user_id != user_id and not resource.is_public:
            raise ForbiddenError("无权访问此资料库")
        return resource

    async def list_by_user(self, user_id: int) -> list[ResourceView]:
        rows = await self.session.scalars(
            select(Resource).where(Resource.user_id == user_id).order_by(Resource.created_at.desc())
        )
        return [_view(resource) for resource in rows]

    async def list_public(self, user_id: int) -> list[ResourceView]:
        """公共资料库：公开的、且不是自己上传的那份。"""
        rows = await self.session.scalars(
            select(Resource)
            .where(Resource.is_public.is_(True), Resource.user_id != user_id)
            .order_by(Resource.created_at.desc())
        )
        return [_view(resource) for resource in rows]

    async def upload(
        self,
        user_id: int,
        username: str,
        *,
        name: str,
        filename: str,
        mimetype: str,
        data: bytes,
        subject: str = "",
        grade: str = "",
        is_public: bool = False,
    ) -> ResourceView:
        key = self.storage.new_key("resource", mimetype, filename)
        await self.storage.put(key, data, mimetype)
        resource = Resource(
            name=name,
            subject=subject,
            grade=grade,
            is_public=is_public,
            original_name=filename[:ORIGINAL_NAME_MAX],
            mimetype=mimetype[:100],
            size_bytes=len(data),
            user_id=user_id,
            creator_name=username[:CREATOR_NAME_MAX],
            storage_key=key,
        )
        self.session.add(resource)
        await self.session.flush()
        logger.info("资料上传成功 id=%s key=%s 大小=%s", resource.id, key, len(data))
        return _view(resource)

    async def update(self, resource_id: int, user_id: int, data: ResourceUpdate) -> ResourceView:
        resource = await self._owned_by(resource_id, user_id, "编辑")
        fields = data.model_dump(exclude_unset=True)
        for key in ("name", "subject", "grade", "is_public"):
            if fields.get(key) is not None:
                setattr(resource, key, fields[key])
        await self.session.flush()
        return _view(resource)

    async def remove(self, resource_id: int, user_id: int) -> dict[str, int]:
        resource = await self._owned_by(resource_id, user_id, "删除")
        await self.session.delete(resource)
        # 先删行再删对象：反过来一旦对象删失败，库里就留下一条永远取不到内容的记录
        if resource.storage_key:
            await self.storage.remove(resource.storage_key)
        # 向量分块留着会让检索命中已删文件的内容，必须一起清
        await self.rag.remove_document(resource_id, user_id)
        return {"id": resource_id}

    async def toggle_public(self, resource_id: int, user_id: int) -> ResourceView:
        resource = await self._owned_by(resource_id, user_id, "操作")
        resource.is_public = not resource.is_public
        await self.session.flush()
        return _view(resource)

    async def open_source(self, resource: Resource) -> AsyncIterator[bytes]:
        """出内容：新数据是对象存储的流，历史 bytea 整份读出。"""
        return await open_source(resource, self.storage)

    async def _owned_by(self, resource_id: int, user_id: int, action: str) -> Resource:
        resource = await self.session.get(Resource, resource_id)
        if resource is None:
            raise NotFoundError("资料库不存在")
        if resource.user_id != user_id:
            raise ForbiddenError(f"无权{action}此资料库")
        return resource


def _view(resource: Resource) -> ResourceView:
    return ResourceView.model_validate(resource)


def get_resource_service(session: SessionDep, rag: RagServiceDep, storage: StorageDep) -> ResourceService:
    return ResourceService(session, rag, storage)


ResourceServiceDep = Annotated[ResourceService, Depends(get_resource_service)]
