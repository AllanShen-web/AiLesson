"""模板路由：本人的模板、模板市场与 Word 导入。"""

from __future__ import annotations

from fastapi import APIRouter, UploadFile

from ailan.core.deps import CurrentUserDep
from ailan.core.envelope import EnvelopeRoute
from ailan.core.errors import BadRequestError, TooLargeError
from ailan.templates.import_service import DOCX_SIZE_LIMIT, TemplateImportServiceDep
from ailan.templates.schemas import (
    TemplateCreate,
    TemplateDraft,
    TemplateUpdate,
    TemplateView,
)
from ailan.templates.service import TemplateServiceDep

# 顺序要紧：`/public` 必须排在 `/{template_id}` 之前，否则 "public" 会被当成 id 去转 int
router = APIRouter(prefix="/templates", tags=["模板"], route_class=EnvelopeRoute)

DOCX_MIMETYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        # 不少浏览器/客户端不带具体类型，只给这个
        "application/octet-stream",
    }
)


@router.get("", response_model=list[TemplateView], summary="我的模板")
async def list_my_templates(user: CurrentUserDep, templates: TemplateServiceDep) -> list[TemplateView]:
    return [TemplateView.model_validate(item) for item in await templates.list_own(user.id)]


@router.get("/public", response_model=list[TemplateView], summary="模板市场")
async def list_public_templates(user: CurrentUserDep, templates: TemplateServiceDep) -> list[TemplateView]:
    return [TemplateView.model_validate(item) for item in await templates.list_public(user.id)]


@router.post(
    "/import-docx",
    response_model=TemplateDraft,
    summary="上传 Word 识别成模板草稿",
)
async def import_docx(
    file: UploadFile,
    user: CurrentUserDep,
    importer: TemplateImportServiceDep,
) -> TemplateDraft:
    """只返回草稿，不落库、原文件也不保存：教师在编辑器里确认后才走新建接口。"""
    name = file.filename or ""
    if not name.lower().endswith(".docx") or (file.content_type or "") not in DOCX_MIMETYPES:
        raise BadRequestError("只允许上传 .docx 文件（老式 .doc 请先另存为 .docx）")

    data = await file.read()
    if not data:
        raise BadRequestError("未收到上传文件")
    if len(data) > DOCX_SIZE_LIMIT:
        raise TooLargeError("Word 文件不能超过 20MB")

    return await importer.parse_draft(data, name)


@router.get("/{template_id}", response_model=TemplateView, summary="模板详情")
async def get_template(template_id: int, user: CurrentUserDep, templates: TemplateServiceDep) -> TemplateView:
    return TemplateView.model_validate(await templates.find_visible(template_id, user.id))


@router.post("", response_model=TemplateView, status_code=201, summary="新建模板")
async def create_template(
    payload: TemplateCreate, user: CurrentUserDep, templates: TemplateServiceDep
) -> TemplateView:
    created = await templates.create(user.id, user.username, payload)
    return TemplateView.model_validate(created)


@router.put("/{template_id}", response_model=TemplateView, summary="修改模板")
async def update_template(
    template_id: int, payload: TemplateUpdate, user: CurrentUserDep, templates: TemplateServiceDep
) -> TemplateView:
    # 归属与"官方模板不可改"都在 service 里判：公开的模板不等于人人可改，
    # "能看"和"能改"是两套口径，放进同一个接口层守卫只会把两者搅在一起
    return TemplateView.model_validate(await templates.update(template_id, user.id, payload))


@router.patch("/{template_id}/public", response_model=TemplateView, summary="公开 / 取消公开")
async def toggle_public(
    template_id: int, user: CurrentUserDep, templates: TemplateServiceDep
) -> TemplateView:
    return TemplateView.model_validate(await templates.toggle_public(template_id, user.id))


@router.post("/{template_id}/copy", response_model=TemplateView, status_code=201, summary="复制成我的模板")
async def copy_template(
    template_id: int, user: CurrentUserDep, templates: TemplateServiceDep
) -> TemplateView:
    clone = await templates.copy(template_id, user.id, user.username)
    return TemplateView.model_validate(clone)


@router.delete("/{template_id}", summary="删除模板")
async def remove_template(
    template_id: int, user: CurrentUserDep, templates: TemplateServiceDep
) -> dict[str, int]:
    await templates.remove(template_id, user.id)
    return {"id": template_id}
