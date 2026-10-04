"""资料路由：列表、上传、预览与下载。

预览和下载直接回二进制，不套 `{code, data}` 外壳（`EnvelopeRoute` 按 media_type 自动放行）；
公开资料可以让浏览器和 CDN 长期缓存，私有资料必须 `no-store`——否则换一个人登录，
拿到的还是上一个人缓存里的文件。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from ailan.core.deps import CurrentUserDep
from ailan.core.envelope import EnvelopeRoute
from ailan.core.errors import BadRequestError, TooLargeError
from ailan.resources.schemas import UPLOAD_SIZE_LIMIT, ResourceUpdate, ResourceView, is_allowed_mimetype
from ailan.resources.service import ResourceServiceDep

router = APIRouter(prefix="/resources", tags=["资料库"], route_class=EnvelopeRoute)

PRIVATE_NO_STORE = "private, no-store"
PUBLIC_LONG_CACHE = "public, max-age=31536000"


@router.get("", response_model=list[ResourceView], summary="我的资料")
async def list_my(user: CurrentUserDep, resources: ResourceServiceDep) -> list[ResourceView]:
    return await resources.list_by_user(user.id)


@router.get("/public", response_model=list[ResourceView], summary="公共资料库")
async def list_public(user: CurrentUserDep, resources: ResourceServiceDep) -> list[ResourceView]:
    return await resources.list_public(user.id)


@router.post("/upload", response_model=ResourceView, status_code=201, summary="上传资料")
async def upload(
    user: CurrentUserDep,
    resources: ResourceServiceDep,
    file: UploadFile = File(...),
    name: str = Form(..., min_length=1, max_length=100),
    subject: str = Form("", max_length=20),
    grade: str = Form("", max_length=20),
    is_public: bool = Form(False),
) -> ResourceView:
    mimetype = file.content_type or "application/octet-stream"
    if not is_allowed_mimetype(mimetype):
        raise BadRequestError("不支持的文件格式")

    data = await file.read()
    if not data:
        raise BadRequestError("未收到上传文件")
    if len(data) > UPLOAD_SIZE_LIMIT:
        raise TooLargeError("文件不能超过 50MB")

    return await resources.upload(
        user.id,
        user.username,
        name=name,
        filename=file.filename or "未命名文件",
        mimetype=mimetype,
        data=data,
        subject=subject,
        grade=grade,
        is_public=is_public,
    )


@router.get("/{resource_id}", response_model=ResourceView, summary="资料详情")
async def get_one(resource_id: int, user: CurrentUserDep, resources: ResourceServiceDep) -> ResourceView:
    return ResourceView.model_validate(await resources.find_visible(resource_id, user.id))


@router.get("/{resource_id}/preview", summary="在线预览")
async def preview(resource_id: int, user: CurrentUserDep, resources: ResourceServiceDep) -> StreamingResponse:
    resource = await resources.find_visible(resource_id, user.id)
    return StreamingResponse(
        await resources.open_source(resource),
        media_type=resource.mimetype,
        headers={"Cache-Control": _cache_control(resource.is_public)},
    )


@router.get("/{resource_id}/download", summary="下载原始文件")
async def download(
    resource_id: int, user: CurrentUserDep, resources: ResourceServiceDep
) -> StreamingResponse:
    resource = await resources.find_visible(resource_id, user.id)
    # 中文名要靠 RFC 5987 的 filename* 才能不被浏览器按 Latin-1 猜；filename 留个同名兜底
    quoted = quote(resource.original_name, safe="")
    return StreamingResponse(
        await resources.open_source(resource),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": _cache_control(resource.is_public),
            "Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}",
        },
    )


@router.put("/{resource_id}", response_model=ResourceView, summary="修改资料信息")
async def update(
    resource_id: int, payload: ResourceUpdate, user: CurrentUserDep, resources: ResourceServiceDep
) -> ResourceView:
    return await resources.update(resource_id, user.id, payload)


@router.patch("/{resource_id}/public", response_model=ResourceView, summary="公开 / 取消公开")
async def toggle_public(
    resource_id: int, user: CurrentUserDep, resources: ResourceServiceDep
) -> ResourceView:
    return await resources.toggle_public(resource_id, user.id)


@router.delete("/{resource_id}", summary="删除资料")
async def remove(resource_id: int, user: CurrentUserDep, resources: ResourceServiceDep) -> dict[str, int]:
    await resources.remove(resource_id, user.id)
    return {"id": resource_id}


def _cache_control(is_public: bool) -> str:
    return PUBLIC_LONG_CACHE if is_public else PRIVATE_NO_STORE
