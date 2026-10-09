"""助手路由：两条流式对话、会话与消息、产出文件、教案导出和管理页配置。

两条流式入口共用 `sse_response`：`stream` 是纯问答，`agent` 带工具与教案上下文。
它们的返回值都是 `text/event-stream`，不套统一外壳（`EnvelopeRoute` 按 media_type 放行）。

配置与头像读写是管理页专属，挂权限码；对话与文件是人人可用的个人数据，只认登录身份，
归属一律在服务里按 `user_id` 过滤——别人的编号在这层拿不到任何东西。

头像一读一写共用 `avatar`：上传要权限码，取图给管理页的 `<img>` 直接引用，所以 GET 不开鉴权。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import StreamingResponse

from ailan.ai_chat.agent.service import AgentServiceDep
from ailan.ai_chat.config_service import AssistantConfigServiceDep
from ailan.ai_chat.schemas import (
    AVATAR_MAX_BYTES,
    AgentChatInput,
    AssistantConfigUpdate,
    AssistantConfigView,
    ConnectionTestResult,
    ConversationView,
    GeneratedFileView,
    MessageView,
    StreamChatInput,
)
from ailan.ai_chat.service import ChatServiceDep, stream_chat
from ailan.ai_chat.sse import sse_response
from ailan.core.deps import CurrentUserDep, require_permission
from ailan.core.envelope import EnvelopeRoute
from ailan.core.errors import BadRequestError, NotFoundError, TooLargeError
from ailan.resources.schemas import UPLOAD_SIZE_LIMIT, ResourceView, is_allowed_mimetype
from ailan.resources.service import ResourceServiceDep
from ailan.storage.service import StorageDep

router = APIRouter(prefix="/ai-chat", tags=["AI 助手"], route_class=EnvelopeRoute)

# 头像按 id 定版，改一次才会换；一天的缓存足够让管理页改完立刻见效
_AVATAR_CACHE = "public, max-age=86400"
_PRIVATE_NO_STORE = "private, no-store"


# ── 流式对话 ──


@router.post("/stream", summary="流式对话（不带工具）")
async def stream(payload: StreamChatInput, user: CurrentUserDep, storage: StorageDep) -> StreamingResponse:
    return sse_response(lambda emit: stream_chat(payload, user.id, storage, emit))


@router.post("/agent", summary="教案助手（工具循环）")
async def agent(
    payload: AgentChatInput, user: CurrentUserDep, assistant: AgentServiceDep
) -> StreamingResponse:
    return sse_response(lambda emit: assistant.run(payload, user, emit))


# ── 会话与消息 ──


@router.get("/conversations", response_model=list[ConversationView], summary="我的会话列表")
async def list_conversations(user: CurrentUserDep, chats: ChatServiceDep) -> list[ConversationView]:
    return await chats.list_conversations(user.id)


@router.delete("/conversations/{conversation_id}", summary="删除会话")
async def delete_conversation(
    conversation_id: int, user: CurrentUserDep, chats: ChatServiceDep
) -> dict[str, int]:
    return await chats.delete_conversation(conversation_id, user.id)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageView],
    summary="会话内的消息",
)
async def list_messages(
    conversation_id: int, user: CurrentUserDep, chats: ChatServiceDep
) -> list[MessageView]:
    return await chats.list_messages(conversation_id, user.id)


# ── 管理页配置 ──


@router.get(
    "/config",
    response_model=AssistantConfigView | None,
    summary="助手配置",
    dependencies=[Depends(require_permission("ai_config:view"))],
)
async def get_config(configs: AssistantConfigServiceDep) -> AssistantConfigView | None:
    return await configs.view_for_admin()


@router.put(
    "/config",
    response_model=AssistantConfigView,
    summary="保存助手配置",
    dependencies=[Depends(require_permission("ai_config:update"))],
)
async def update_config(
    payload: AssistantConfigUpdate, configs: AssistantConfigServiceDep
) -> AssistantConfigView:
    return await configs.update(payload)


@router.post(
    "/config/test",
    response_model=ConnectionTestResult,
    summary="连通性测试",
    dependencies=[Depends(require_permission("ai_config:view"))],
)
async def test_connection(configs: AssistantConfigServiceDep) -> ConnectionTestResult:
    return await configs.test_connection()


@router.post(
    "/avatar",
    summary="上传助手头像",
    dependencies=[Depends(require_permission("ai_config:upload_avatar"))],
)
async def upload_avatar(configs: AssistantConfigServiceDep, file: UploadFile = File(...)) -> dict[str, bool]:
    mimetype = file.content_type or ""
    if not mimetype.startswith("image/"):
        raise BadRequestError("只能上传图片文件")
    data = await file.read()
    if not data:
        raise BadRequestError("未收到上传文件")
    if len(data) > AVATAR_MAX_BYTES:
        raise TooLargeError("头像不能超过 5MB")
    await configs.set_avatar(data, mimetype)
    return {"success": True}


@router.get("/avatar", summary="助手头像")
async def get_avatar(configs: AssistantConfigServiceDep) -> Response:
    avatar = await configs.avatar()
    if avatar is None:
        raise NotFoundError("尚未设置助手头像")
    data, mimetype = avatar
    return Response(content=data, media_type=mimetype, headers={"Cache-Control": _AVATAR_CACHE})


# ── 附件、产出文件与导出 ──


@router.post("/upload", response_model=ResourceView, summary="上传对话附件")
async def upload_attachment(
    user: CurrentUserDep, resources: ResourceServiceDep, file: UploadFile = File(...)
) -> ResourceView:
    """附件进的是资料库：助手读它、检索它都走资料那套，不为对话再建一份存储。"""
    mimetype = file.content_type or "application/octet-stream"
    if not is_allowed_mimetype(mimetype):
        raise BadRequestError("不支持的文件格式")
    data = await file.read()
    if not data:
        raise BadRequestError("未收到上传文件")
    if len(data) > UPLOAD_SIZE_LIMIT:
        raise TooLargeError("文件不能超过 50MB")

    filename = file.filename or "未命名文件"
    return await resources.upload(
        user.id, user.username, name=filename, filename=filename, mimetype=mimetype, data=data
    )


@router.get("/files", response_model=list[GeneratedFileView], summary="助手生成的文件")
async def list_files(user: CurrentUserDep, chats: ChatServiceDep) -> list[GeneratedFileView]:
    return await chats.list_generated_files(user.id)


@router.get("/files/{file_id}", response_model=GeneratedFileView, summary="文件信息")
async def file_info(file_id: int, user: CurrentUserDep, chats: ChatServiceDep) -> GeneratedFileView:
    return GeneratedFileView.model_validate(await chats.find_generated_file(file_id, user.id))


@router.get("/files/{file_id}/download", summary="下载助手生成的文件")
async def download_file(file_id: int, user: CurrentUserDep, chats: ChatServiceDep) -> StreamingResponse:
    row = await chats.find_generated_file(file_id, user.id)
    return StreamingResponse(
        await chats.open_generated_file(row),
        media_type=row.mimetype,
        headers={
            "Cache-Control": _PRIVATE_NO_STORE,
            "Content-Disposition": _attachment(row.file_name),
        },
    )


@router.delete("/files/{file_id}", summary="删除助手生成的文件")
async def delete_file(file_id: int, user: CurrentUserDep, chats: ChatServiceDep) -> dict[str, int]:
    return await chats.remove_generated_file(file_id, user.id)


@router.get("/export", summary="导出教案")
async def export_lesson_plan(
    user: CurrentUserDep,
    chats: ChatServiceDep,
    lesson_plan_id: int = Query(alias="lessonPlanId"),
    format: str = Query("docx"),
) -> Response:
    file = await chats.export_lesson_plan(lesson_plan_id, user.id, format)
    return Response(
        content=file.content,
        media_type=file.mimetype,
        headers={"Content-Disposition": _attachment(file.file_name)},
    )


def _attachment(file_name: str) -> str:
    """中文名要靠 RFC 5987 的 filename* 才能不被浏览器按 Latin-1 猜；filename 留个同名兜底。"""
    quoted = quote(file_name, safe="")
    return f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"
