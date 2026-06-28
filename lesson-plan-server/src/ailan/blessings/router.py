"""祝福墙路由。

写留言和读公开墙都不需要登录，所以这一组接口只有改自己的墙、下架自己的留言才挂 `CurrentUserDep`。
"""

from __future__ import annotations

from fastapi import APIRouter, UploadFile

from ailan.blessings.models import Blessing, BlessingSetting
from ailan.blessings.schemas import (
    BlessingCreate,
    BlessingRecall,
    BlessingSettingUpdate,
    BlessingSettingView,
    BlessingView,
    PublicBlessingSetting,
)
from ailan.blessings.service import BlessingServiceDep, BlessingSettingServiceDep
from ailan.core.deps import CurrentUserDep
from ailan.core.envelope import EnvelopeRoute
from ailan.core.errors import BadRequestError, TooLargeError
from ailan.images.schemas import ImageRef
from ailan.images.service import ImageServiceDep

# 顺序要紧：`/upload`、`/mine` 这些字面量要排在 `/{blessing_id}` 之前
router = APIRouter(prefix="/blessings", tags=["祝福墙"], route_class=EnvelopeRoute)
settings_router = APIRouter(prefix="/blessing-settings", tags=["祝福墙配置"], route_class=EnvelopeRoute)

ALLOWED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
IMAGE_SIZE_LIMIT = 10 * 1024 * 1024


@router.get("/mine", response_model=list[BlessingView], summary="我收到与发出的留言")
async def list_mine(user: CurrentUserDep, blessings: BlessingServiceDep) -> list[BlessingView]:
    return [_view(b) for b in await blessings.list_for_user(user.id, user.username)]


@router.get("/users/{username}", response_model=list[BlessingView], summary="某人的公开祝福墙")
async def list_public_wall(username: str, blessings: BlessingServiceDep) -> list[BlessingView]:
    return [_view(b) for b in await blessings.list_visible_by_username(username)]


@router.post("", response_model=BlessingView, status_code=201, summary="送一条祝福")
async def create(payload: BlessingCreate, blessings: BlessingServiceDep) -> BlessingView:
    return _view(await blessings.create(payload))


@router.post("/upload", response_model=ImageRef, summary="上传留言配图")
async def upload_image(file: UploadFile, images: ImageServiceDep) -> ImageRef:
    mimetype = file.content_type or ""
    if mimetype not in ALLOWED_IMAGE_TYPES:
        raise BadRequestError("只允许上传 jpg/png/gif/webp 格式图片")
    data = await file.read()
    if not data:
        raise BadRequestError("未收到上传文件")
    if len(data) > IMAGE_SIZE_LIMIT:
        raise TooLargeError("图片不能超过 10MB")
    return await images.save(file.filename or "图片", mimetype, data)


@router.patch("/{blessing_id}/hidden", response_model=BlessingView, summary="下架 / 恢复自己的留言")
async def toggle_hidden(
    blessing_id: int, user: CurrentUserDep, blessings: BlessingServiceDep
) -> BlessingView:
    return _view(await blessings.toggle_hidden(blessing_id, user.id))


@router.delete("/{blessing_id}", summary="删除自己的留言")
async def remove(blessing_id: int, user: CurrentUserDep, blessings: BlessingServiceDep) -> dict[str, int]:
    return await blessings.remove(blessing_id, user.id)


@router.post("/{blessing_id}/recall", summary="匿名撤回刚发的那条")
async def recall(blessing_id: int, payload: BlessingRecall, blessings: BlessingServiceDep) -> dict[str, int]:
    return await blessings.remove_by_temp_id(blessing_id, payload.temp_id)


@settings_router.get("/mine", response_model=BlessingSettingView, summary="我的墙面配置")
async def my_setting(user: CurrentUserDep, settings: BlessingSettingServiceDep) -> BlessingSettingView:
    return _setting_view(await settings.ensure_for_user(user.id, user.nickname or user.username))


@settings_router.get("/users/{username}", response_model=PublicBlessingSetting | None, summary="公开墙面配置")
async def public_setting(username: str, settings: BlessingSettingServiceDep) -> PublicBlessingSetting | None:
    found = await settings.find_by_username(username)
    if found is None:
        return None
    setting, user = found
    return PublicBlessingSetting.model_validate(
        _setting_view(setting).model_dump() | {"username": user.username, "nickname": user.nickname}
    )


@settings_router.put("", response_model=BlessingSettingView, summary="保存墙面配置")
async def update_setting(
    payload: BlessingSettingUpdate, user: CurrentUserDep, settings: BlessingSettingServiceDep
) -> BlessingSettingView:
    return _setting_view(await settings.update(user.id, payload.settings))


@settings_router.post("/toggle-enabled", response_model=BlessingSettingView, summary="开启 / 关闭祝福墙")
async def toggle_enabled(user: CurrentUserDep, settings: BlessingSettingServiceDep) -> BlessingSettingView:
    return _setting_view(await settings.toggle_enabled(user.id))


def _view(blessing: Blessing) -> BlessingView:
    return BlessingView.model_validate(blessing)


def _setting_view(setting: BlessingSetting) -> BlessingSettingView:
    return BlessingSettingView.model_validate(setting)
