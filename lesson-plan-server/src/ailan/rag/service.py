"""知识库（RAG）服务：抽取文本 → 切块 → 嵌入 → 余弦检索。

向量化是一次几十秒到几分钟的慢任务，接口不能陪着等：`start_vectorization` 只做校验、
把状态置成 processing 并提交，真正的摄取交给后台任务，前端轮询状态。
后台任务必须自己开一个会话——请求会话在响应发出后就归还连接池了。
"""

from __future__ import annotations

import logging
from typing import Annotated, cast

from fastapi import BackgroundTasks, Depends
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.ai_chat.config_service import AssistantConfigService, AssistantConfigServiceDep
from ailan.core.db import SessionDep, SessionFactory
from ailan.core.errors import BadRequestError, ForbiddenError, NotFoundError
from ailan.llm.factory import ModelSpec, build_embeddings
from ailan.rag.chunking import chunk_text
from ailan.rag.extract import extract_document_text
from ailan.rag.models import EMBEDDING_DIMENSIONS, KnowledgeChunk, KnowledgeDocument
from ailan.rag.schemas import DocumentStatus, ExtractionPreview, RagHit, VectorizationResult
from ailan.resources.files import read_bytes
from ailan.resources.models import Resource
from ailan.storage.service import StorageDep, StorageService, get_storage

logger = logging.getLogger(__name__)

# 提取文本上限：约 300 页书。再大文档 embedding 花费和等待时间都没有意义
MAX_TEXT_CHARS = 300_000
# 嵌入接口单批条数上限（DashScope text-embedding-v3 一次最多 10 条）
EMBED_BATCH_SIZE = 10
# 只滤掉明显无关的命中。不同嵌入模型的余弦距离分布差别很大（正常命中约 0.5~0.6），
# 阈值放宽松，命中内容是否真的相关交给模型判断
DISTANCE_THRESHOLD = 0.75

STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"
# 错误信息列宽 500，留出余量截断
ERROR_MESSAGE_MAX = 480

EXTRACTION_FAILED_MESSAGE = (
    "未能从文件提取到文本内容，当前支持 PDF、Word(.docx)、txt 及纯文本格式（纯图片的扫描件 PDF 暂不支持）"
)

# 后台任务没有队列，进程一重启就全丢了；这些行只能置为失败让用户重新发起
STALE_RESET_MESSAGE = "服务重启中断了向量化，请重新发起"


class RagService:
    def __init__(
        self,
        session: AsyncSession,
        configs: AssistantConfigService,
        storage: StorageService,
        background: BackgroundTasks,
    ) -> None:
        self.session = session
        self.configs = configs
        self.storage = storage
        self.background = background

    async def preview_extraction(self, resource_id: int, user_id: int) -> ExtractionPreview:
        """向量化前先把要喂进去的文本给用户看一遍。

        有确认过的编辑文本就直接返回它，否则现场解析文件——纯本地计算，不花钱。
        """
        resource = await self._owned_resource(resource_id, user_id)
        document = await self._document_of(resource_id)
        text = document.edited_text if document and document.edited_text else await self._extract(resource)
        text = (text or "").strip()
        if not text:
            raise BadRequestError(EXTRACTION_FAILED_MESSAGE)
        _assert_within_limit(text, MAX_TEXT_CHARS)
        pieces = chunk_text(text)
        return ExtractionPreview(char_count=len(text), chunk_count=len(pieces), text=text)

    async def start_vectorization(
        self, resource_id: int, user_id: int, text: str | None = None
    ) -> VectorizationResult:
        """发起（或重新发起）向量化：本次确认文本 > 历史确认文本 > 现场解析文件。"""
        # 只为归属校验调它：不是自己上传的文件就在这里被挡掉，返回值用不上
        await self._owned_resource(resource_id, user_id)
        if text is not None and not text.strip():
            raise BadRequestError("确认文本不能为空")
        if text is not None and len(text) > MAX_TEXT_CHARS:
            raise BadRequestError(f"文本过大（{len(text)} 字，上限 {MAX_TEXT_CHARS} 字）")

        # 先验模型配置：任务已经排队、跑了一半才发现没配 Key，用户只会看到一个失败的进度条
        spec = await self.configs.spec_or_throw()

        document = await self._document_of(resource_id)
        if document is not None and document.status == STATUS_PROCESSING:
            return VectorizationResult(status=document.status)

        if document is None:
            document = KnowledgeDocument(resource_id=resource_id, user_id=user_id)
            self.session.add(document)
        if text is not None:
            document.edited_text = text
        document.status = STATUS_PROCESSING
        document.error_message = None
        document.chunk_count = 0
        document.embedding_model = spec.embedding_model_name
        await self.session.flush()
        document_id = document.id

        confirmed_text = text if text is not None else document.edited_text
        # 响应返回后才跑：请求会话此时已提交，后台任务才查得到刚置为 processing 的这行
        self.background.add_task(ingest_document, document_id, resource_id, confirmed_text, spec)
        return VectorizationResult(status=STATUS_PROCESSING)

    async def ingest(
        self, document_id: int, resource_id: int, confirmed_text: str | None, spec: ModelSpec
    ) -> None:
        """摄取：切块 → 批量嵌入 → 落库，并把任务状态推到 ready。"""
        resource = await self.session.get(Resource, resource_id)
        if resource is None:
            raise NotFoundError("资料库文件不存在")

        text = confirmed_text or await self._extract(resource)
        if not text or not text.strip():
            raise BadRequestError(EXTRACTION_FAILED_MESSAGE)
        if len(text) > MAX_TEXT_CHARS:
            raise BadRequestError(
                f"文档过大（提取文本 {len(text)} 字，上限 {MAX_TEXT_CHARS} 字），请拆分后再向量化"
            )

        pieces = chunk_text(text)
        if not pieces:
            raise BadRequestError("文档切块结果为空")

        embeddings = build_embeddings(spec)
        # 重新向量化要先清旧块，否则新文本的块会和旧块混在一起被检索到
        await self.session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))
        for start in range(0, len(pieces), EMBED_BATCH_SIZE):
            batch = pieces[start : start + EMBED_BATCH_SIZE]
            vectors = await embeddings.aembed_documents(batch)
            self.session.add_all(
                [
                    KnowledgeChunk(
                        document_id=document_id,
                        resource_id=resource_id,
                        user_id=resource.user_id,
                        chunk_index=start + offset,
                        content=content,
                        embedding=vector,
                    )
                    for offset, (content, vector) in enumerate(zip(batch, vectors, strict=True))
                ]
            )
            # 每批落一次：中途失败时库里至少留下已完成的部分，状态仍是 processing 不会假装成功
            await self.session.flush()

        document = await self.session.get(KnowledgeDocument, document_id)
        if document is not None:
            document.status = STATUS_READY
            document.chunk_count = len(pieces)
        logger.info("向量化完成 resource=%s 分块=%s", resource_id, len(pieces))

    async def mark_failed(self, document_id: int, message: str) -> None:
        await self.session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id)
            .values(status=STATUS_FAILED, error_message=message[:ERROR_MESSAGE_MAX])
        )

    async def statuses(self, resource_ids: list[int], user_id: int) -> list[DocumentStatus]:
        if not resource_ids:
            return []
        rows = await self.session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.resource_id.in_(resource_ids), KnowledgeDocument.user_id == user_id
            )
        )
        return [
            DocumentStatus(
                resource_id=document.resource_id,
                status=document.status,
                chunk_count=document.chunk_count,
                error_message=document.error_message,
            )
            for document in rows
        ]

    async def remove_document(self, resource_id: int, user_id: int) -> None:
        """删资料时连带删任务与分块，避免检索命中孤儿内容。"""
        document_ids = list(
            await self.session.scalars(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.resource_id == resource_id, KnowledgeDocument.user_id == user_id
                )
            )
        )
        if not document_ids:
            return
        await self.session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id.in_(document_ids)))
        await self.session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(document_ids)))

    async def has_ready_content(self, user_id: int) -> bool:
        """Agent 据此决定要不要注册语义检索工具：没有可用内容就别让模型去调一个空库。"""
        found = await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(KnowledgeDocument.status == STATUS_READY, KnowledgeDocument.user_id == user_id)
        )
        return bool(found)

    async def search(self, query: str, user_id: int, top_k: int = 5) -> list[RagHit]:
        """语义检索：本人分块 + 公开资料分块，按余弦距离取前 top_k。"""
        spec = await self.configs.spec_or_throw()
        vector = await build_embeddings(spec).aembed_query(query)
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise BadRequestError(
                f"嵌入模型返回维度 {len(vector)}，与向量表 {EMBEDDING_DIMENSIONS} 不匹配，请检查向量模型配置"
            )

        distance = KnowledgeChunk.embedding.cosine_distance(vector)
        rows = (
            await self.session.execute(
                select(
                    KnowledgeChunk.resource_id,
                    Resource.name,
                    KnowledgeChunk.chunk_index,
                    KnowledgeChunk.content,
                    distance.label("distance"),
                )
                .join(Resource, Resource.id == KnowledgeChunk.resource_id)
                .where(or_(KnowledgeChunk.user_id == user_id, Resource.is_public.is_(True)))
                .order_by(distance)
                .limit(top_k)
            )
        ).all()

        # 内层 join 已经滤掉了资料被删的孤儿分块；这里只按距离收口
        return [
            RagHit(
                resource_id=row.resource_id,
                resource_name=row.name or "",
                chunk_index=row.chunk_index,
                content=row.content,
                distance=round(float(row.distance), 4),
            )
            for row in rows
            if row.distance is not None and float(row.distance) <= DISTANCE_THRESHOLD
        ]

    async def _owned_resource(self, resource_id: int, user_id: int) -> Resource:
        resource = await self.session.get(Resource, resource_id)
        if resource is None:
            raise NotFoundError("资料库文件不存在")
        if resource.user_id != user_id:
            raise ForbiddenError("只能向量化自己上传的文件")
        return resource

    async def _document_of(self, resource_id: int) -> KnowledgeDocument | None:
        return await self.session.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.resource_id == resource_id)
        )

    async def _extract(self, resource: Resource) -> str:
        data = await read_bytes(resource, self.storage)
        return extract_document_text(resource.mimetype, resource.original_name, data) or ""


EXTRACTION_FAILED_MESSAGE = (
    "未能从文件提取到文本内容，当前支持 PDF、Word(.docx)、txt 及纯文本格式（纯图片的扫描件 PDF 暂不支持）"
)


async def ingest_document(
    document_id: int, resource_id: int, confirmed_text: str | None, spec: ModelSpec
) -> None:
    """后台摄取：自己开一个会话，失败只落状态。

    请求会话在响应发出后就归还连接池了，接着用它写会把数据写进一个已经结束的事务。
    """
    storage = get_storage()
    async with SessionFactory() as session:
        # 这里的 BackgroundTasks 只是占位：摄取实例不会再排新任务
        service = RagService(session, AssistantConfigService(session, storage), storage, BackgroundTasks())
        try:
            await service.ingest(document_id, resource_id, confirmed_text, spec)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning("向量化失败 document=%s resource=%s: %s", document_id, resource_id, exc)
            await service.mark_failed(document_id, str(exc) or "向量化失败")
            await session.commit()


async def reset_interrupted_tasks() -> None:
    """启动时把上次进程遗留的 processing 置为 failed。"""
    async with SessionFactory() as session:
        # `execute` 的返回类型只声明到 Result，`rowcount` 要 DML 之后才有；这里必然是一条 UPDATE
        result = cast(
            "CursorResult[int]",
            await session.execute(
                update(KnowledgeDocument)
                .where(KnowledgeDocument.status == STATUS_PROCESSING)
                .values(status=STATUS_FAILED, error_message=STALE_RESET_MESSAGE)
            ),
        )
        await session.commit()
    if result.rowcount:
        logger.info("已重置 %s 条被重启打断的向量化任务", result.rowcount)


def _assert_within_limit(text: str, limit: int) -> None:
    if len(text) > limit:
        raise BadRequestError(f"文档过大（{len(text)} 字，上限 {limit} 字），请拆分后再向量化")


def get_rag_service(
    session: SessionDep,
    configs: AssistantConfigServiceDep,
    storage: StorageDep,
    background: BackgroundTasks,
) -> RagService:
    return RagService(session, configs, storage, background)


RagServiceDep = Annotated[RagService, Depends(get_rag_service)]
