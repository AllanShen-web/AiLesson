"""课件路由：列表、导入、编辑、导出预览下载，以及发起改写任务。

课件是个人的，权限只到"登录"这一层，不再挂权限码：能进课件库菜单的人，本来就都能
建、改、删自己的课件。公开/私有由记录上的 `is_public` 决定，跨用户读取一律走 `get_visible`。

导出、预览、下载直接回二进制，不套 `{code, data}` 外壳（`EnvelopeRoute` 按 media_type 自动放行）；
预览图按记录归属设缓存——同一个用户刷页面不该每次都重渲一遍。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Query, Response, UploadFile
from fastapi.responses import FileResponse

from ailan.core.deps import CurrentUserDep
from ailan.core.envelope import EnvelopeRoute
from ailan.core.errors import BadRequestError, TooLargeError
from ailan.core.schemas import Page
from ailan.ppt.exporter import PPTX_MIMETYPE
from ailan.ppt.schemas import (
    TEMPLATE_SIZE_LIMIT,
    PresentationCreate,
    PresentationQueryDep,
    PresentationUpdate,
    PresentationView,
    Revision,
    RewriteInput,
    RewriteTaskView,
)
from ailan.ppt.service import PresentationServiceDep

router = APIRouter(prefix="/ppt", tags=["课件"], route_class=EnvelopeRoute)

PNG_MEDIA_TYPE = "image/png"
PRIVATE_PREVIEW_CACHE = "private, max-age=3600"


@router.get("", response_model=Page[PresentationView], summary="课件库")
async def list_accessible(
    user: CurrentUserDep, presentations: PresentationServiceDep, query: PresentationQueryDep
) -> Page[PresentationView]:
    return await presentations.list_accessible(user.id, query)


@router.get("/mine", response_model=list[PresentationView], summary="我的课件")
async def list_mine(
    user: CurrentUserDep, presentations: PresentationServiceDep, query: PresentationQueryDep
) -> list[PresentationView]:
    return await presentations.list_mine(user.id, query)


@router.get("/public", response_model=list[PresentationView], summary="公共课件库")
async def list_public(user: CurrentUserDep, presentations: PresentationServiceDep) -> list[PresentationView]:
    return await presentations.list_public(user.id)


@router.get("/rewrite-tasks", response_model=list[RewriteTaskView], summary="我发起过的改写任务")
async def list_rewrite_tasks(
    user: CurrentUserDep, presentations: PresentationServiceDep
) -> list[RewriteTaskView]:
    """整册改写要跑几分钟，前端靠这个接口轮询进度；任务只活在进程里，重启即清。"""
    return presentations.list_rewrite_tasks(user.id)


@router.post("/import-template", response_model=PresentationView, status_code=201, summary="导入 PPTX 模板")
async def import_template(
    user: CurrentUserDep, presentations: PresentationServiceDep, file: UploadFile = File(...)
) -> PresentationView:
    if not _is_pptx(file):
        raise BadRequestError("只允许上传 .pptx 文件")
    data = await file.read()
    if not data:
        raise BadRequestError("未收到上传文件")
    if len(data) > TEMPLATE_SIZE_LIMIT:
        raise TooLargeError("文件不能超过 50MB")
    # 浏览器传来的中文名按 Latin-1 解码会得到一串乱码，先按 UTF-8 还原
    return await presentations.import_template(user.id, user.username, _original_name(file), data)


@router.post("", response_model=PresentationView, status_code=201, summary="新建课件")
async def create(
    user: CurrentUserDep, presentations: PresentationServiceDep, payload: PresentationCreate
) -> PresentationView:
    return await presentations.create(user.id, user.username, payload)


@router.get("/{presentation_id}", response_model=PresentationView, summary="课件详情")
async def get_one(
    presentation_id: int, user: CurrentUserDep, presentations: PresentationServiceDep
) -> PresentationView:
    return await presentations.detail(presentation_id, user.id)


@router.put("/{presentation_id}", response_model=PresentationView, summary="保存课件")
async def update(
    presentation_id: int,
    payload: PresentationUpdate,
    user: CurrentUserDep,
    presentations: PresentationServiceDep,
) -> PresentationView:
    return await presentations.update(presentation_id, user.id, payload)


@router.delete("/{presentation_id}", summary="删除课件")
async def remove(
    presentation_id: int, user: CurrentUserDep, presentations: PresentationServiceDep
) -> dict[str, int]:
    return await presentations.remove(presentation_id, user.id)


@router.post("/{presentation_id}/copy", response_model=PresentationView, status_code=201, summary="复制课件")
async def copy(
    presentation_id: int, user: CurrentUserDep, presentations: PresentationServiceDep
) -> PresentationView:
    return await presentations.copy(presentation_id, user.id, user.username)


@router.patch("/{presentation_id}/public", response_model=PresentationView, summary="公开 / 取消公开")
async def toggle_public(
    presentation_id: int, user: CurrentUserDep, presentations: PresentationServiceDep
) -> PresentationView:
    return await presentations.toggle_public(presentation_id, user.id)


@router.get("/{presentation_id}/export", summary="导出幻灯片文档为 pptx")
async def export_ppt(
    presentation_id: int, user: CurrentUserDep, presentations: PresentationServiceDep
) -> Response:
    data, name = await presentations.export_doc(presentation_id, user.id)
    return _attachment(data, name)


@router.get("/{presentation_id}/download", summary="下载 pptx 原文件")
async def download(
    presentation_id: int, user: CurrentUserDep, presentations: PresentationServiceDep
) -> Response:
    data, name = await presentations.download(presentation_id, user.id)
    return _attachment(data, name)


@router.get("/{presentation_id}/preview/{page}", summary="逐页预览图")
async def preview(
    presentation_id: int,
    page: int,
    user: CurrentUserDep,
    presentations: PresentationServiceDep,
    revision: Revision = Query("working", description="看改写产物还是模板外壳"),
) -> FileResponse:
    """必须是 GET：预览图直接进 `<img src>`，改成 POST 浏览器就画不出来。"""
    path = await presentations.preview_page(presentation_id, user.id, page, revision)
    return FileResponse(path, media_type=PNG_MEDIA_TYPE, headers={"Cache-Control": PRIVATE_PREVIEW_CACHE})


@router.post(
    "/{presentation_id}/ai-rewrite", response_model=RewriteTaskView, status_code=202, summary="按教案改写模板"
)
async def ai_rewrite(
    presentation_id: int,
    payload: RewriteInput,
    user: CurrentUserDep,
    presentations: PresentationServiceDep,
) -> RewriteTaskView:
    """参数当场校验，改写排进后台队列，返回的任务给前端轮询进度。"""
    return await presentations.start_rewrite(presentation_id, user.id, user.username, payload)


def _is_pptx(file: UploadFile) -> bool:
    """认后缀也认 MIME：上传组件给的 content_type 常常是空的或 octet-stream。"""
    name = (file.filename or "").lower()
    if not name.endswith(".pptx"):
        return False
    mimetype = file.content_type or ""
    return not mimetype or mimetype in (PPTX_MIMETYPE, "application/octet-stream")


def _original_name(file: UploadFile) -> str:
    name = file.filename or "导入的课件.pptx"
    try:
        return name.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return name


def _attachment(data: bytes, name: str) -> Response:
    """中文名要靠 RFC 5987 的 filename* 才不被浏览器按 Latin-1 猜；filename 留个同名兜底。"""
    quoted = quote(name, safe="")
    return Response(
        content=data,
        media_type=PPTX_MIMETYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"},
    )
