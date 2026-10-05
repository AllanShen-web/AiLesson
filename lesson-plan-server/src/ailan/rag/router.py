"""知识库路由：抽取预览、发起向量化、进度查询与清理。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ailan.core.deps import CurrentUserDep
from ailan.core.envelope import EnvelopeRoute
from ailan.rag.schemas import (
    DocumentStatus,
    ExtractionPreview,
    PreviewInput,
    VectorizationResult,
    VectorizeInput,
)
from ailan.rag.service import RagServiceDep

router = APIRouter(prefix="/rag", tags=["知识库"], route_class=EnvelopeRoute)


@router.post("/documents/preview", response_model=ExtractionPreview, summary="向量化前预览提取文本")
async def preview(payload: PreviewInput, user: CurrentUserDep, rag: RagServiceDep) -> ExtractionPreview:
    return await rag.preview_extraction(payload.resource_id, user.id)


@router.post("/documents", response_model=VectorizationResult, summary="发起向量化")
async def start_vectorization(
    payload: VectorizeInput, user: CurrentUserDep, rag: RagServiceDep
) -> VectorizationResult:
    return await rag.start_vectorization(payload.resource_id, user.id, payload.text)


@router.get("/documents/status", response_model=list[DocumentStatus], summary="批量查询向量化进度")
async def statuses(
    user: CurrentUserDep,
    rag: RagServiceDep,
    resource_ids: list[int] = Query(default=[], alias="resourceIds", description="逗号分隔的资料 id"),
) -> list[DocumentStatus]:
    return await rag.statuses(resource_ids, user.id)


@router.delete("/documents/{resource_id}", summary="清除某份资料的向量数据")
async def remove_document(resource_id: int, user: CurrentUserDep, rag: RagServiceDep) -> dict[str, bool]:
    await rag.remove_document(resource_id, user.id)
    return {"success": True}
