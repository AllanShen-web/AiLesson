"""助手对话服务：会话增删查、历史读写、不带工具的流式对话，以及产出文件的读与删。

工具循环在 `agent`，文件字节怎么生成在 `files`，这一层只管三件事：一条最简单的
`系统提示词 + 历史 + 本轮输入 → 流式回答` 链——没有教案上下文时（助手首页、纯问答）
不需要那套工具，也不必为它付工具 schema 的 token；会话与消息的读写；助手产出的文件。

会话与消息的读写函数被 Agent 复用，所以写成模块级函数而不是私有方法：
两个入口对同一张表只该有一套落库口径。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.ai_chat.config_service import AssistantConfigService
from ailan.ai_chat.files.generator import ExportedFile, generate_file
from ailan.ai_chat.files.layout import LayoutField
from ailan.ai_chat.models import Conversation, GeneratedFile, Message
from ailan.ai_chat.schemas import (
    DEFAULT_CONVERSATION_TITLE,
    TITLE_PREVIEW_CHARS,
    AttachmentRef,
    ConversationView,
    GeneratedFileView,
    MessageView,
    StreamChatInput,
)
from ailan.ai_chat.sse import Emit
from ailan.core.db import SessionDep, SessionFactory
from ailan.core.errors import BadRequestError, NotFoundError
from ailan.lesson_plans.models import LessonPlan
from ailan.llm.factory import build_model_with_fallback
from ailan.llm.text import model_text
from ailan.resources.files import open_source
from ailan.storage.service import StorageDep, StorageService
from ailan.templates.models import Template

logger = logging.getLogger(__name__)

# 纯对话没有工具循环，历史按条数取最近若干条即可：一次问答来回占两条
PLAIN_HISTORY_LIMIT = 20


class ChatService:
    def __init__(self, session: AsyncSession, storage: StorageService) -> None:
        self.session = session
        # 产出文件的字节可能在桶里；请求级会话足够，读文件不用另一条链
        self.storage = storage

    async def list_conversations(self, user_id: int) -> list[ConversationView]:
        rows = await self.session.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return [ConversationView.model_validate(row) for row in rows]

    async def delete_conversation(self, conversation_id: int, user_id: int) -> dict[str, int]:
        conversation = await self.session.scalar(
            select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        )
        if conversation is None:
            raise NotFoundError("会话不存在")
        # 消息要手动删：外键没写 ON DELETE CASCADE，会话下还压着消息就删不掉会话行。
        # 生成文件反过来——它记的是用户产出的东西，删会话不该顺手把文件也丢掉
        await self.session.execute(delete(Message).where(Message.conversation_id == conversation_id))
        await self.session.delete(conversation)
        return {"id": conversation_id}

    async def list_messages(self, conversation_id: int, user_id: int) -> list[MessageView]:
        found = await self.session.scalar(
            select(Conversation.id).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        )
        if found is None:
            raise NotFoundError("会话不存在")
        rows = await self.session.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        )
        return [MessageView.model_validate(row) for row in rows]

    # ── 助手产出的文件 ──

    async def list_generated_files(self, user_id: int) -> list[GeneratedFileView]:
        """资料库页的「AI 生成」列表：只要元信息，字节和对象 key 都不出场。"""
        rows = await generated_files_of(self.session, user_id)
        return [GeneratedFileView.model_validate(row) for row in rows]

    async def find_generated_file(self, file_id: int, user_id: int) -> GeneratedFile:
        """别人的文件按「不存在」处理：列不出、也下不到，不用多一条 403 让编号被人试出来。"""
        row = await self.session.scalar(
            select(GeneratedFile).where(GeneratedFile.id == file_id, GeneratedFile.user_id == user_id)
        )
        if row is None:
            raise NotFoundError("文件不存在")
        return row

    async def open_generated_file(self, row: GeneratedFile) -> AsyncIterator[bytes]:
        return await open_source(row, self.storage)

    async def remove_generated_file(self, file_id: int, user_id: int) -> dict[str, int]:
        row = await self.find_generated_file(file_id, user_id)
        await self.session.delete(row)
        # 先删行再删对象：反过来一旦对象删失败，库里就留下一条永远取不到内容的记录
        if row.storage_key:
            await self.storage.remove(row.storage_key)
        return {"id": file_id}

    # ── 导出 ──

    async def export_lesson_plan(self, plan_id: int, user_id: int, fmt: str) -> ExportedFile:
        """把库里的这一篇导成文件。只许导自己的：公开教案是给同学看的，不是给人下载的。"""
        plan = await self.session.get(LessonPlan, plan_id)
        if plan is None or plan.user_id != user_id:
            raise NotFoundError("教案不存在")

        raw_fields = [item for item in ((plan.content or {}).get("fields") or []) if isinstance(item, dict)]
        labels = {str(f["id"]): str(f["label"]) for f in raw_fields if f.get("id") and f.get("label")}
        # 老教案的快照里没有字段定义（建它的时候还没存快照），排版与栏目名只能回查模板
        if not labels and plan.template_id:
            labels = await self._template_labels(plan.template_id)

        try:
            return generate_file(
                format=fmt,
                title=plan.title or "教案",
                fields=(plan.content or {}).get("values") or {},
                field_labels=labels,
                layout_fields=[LayoutField.from_json(item) for item in raw_fields],
                form_info=self._form_info(plan),
            )
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc

    @staticmethod
    def _form_info(plan: LessonPlan) -> dict[str, str]:
        """封面与抬头上的基本信息：导出器按这里的名字排「课题 / 学科 / 年级」那一行。"""
        return {"topic": plan.title or "", "subject": plan.subject or "", "grade": plan.grade or ""}

    async def _template_labels(self, template_id: int) -> dict[str, str]:
        """模板里的字段名。查不到就当没有——导出这一步不该因为模板被删而失败。"""
        content = await self.session.scalar(select(Template.content).where(Template.id == template_id))
        raw_fields = [item for item in ((content or {}).get("fields") or []) if isinstance(item, dict)]
        return {str(f["id"]): str(f["label"]) for f in raw_fields if f.get("id") and f.get("label")}


async def stream_chat(payload: StreamChatInput, user_id: int, storage: StorageService, emit: Emit) -> None:
    """不带工具的流式对话：逐字推 token，落库一条助手消息，最后 done。

    这一轮自带会话：SSE 的字节是在请求级会话随依赖退出之后才真正发出的，
    和 Agent 那一轮是同一个道理。
    """
    async with SessionFactory() as session:
        configs = AssistantConfigService(session, storage)
        conversation = None
        if payload.conversation_id:
            conversation = await session.scalar(
                select(Conversation).where(
                    Conversation.id == payload.conversation_id, Conversation.user_id == user_id
                )
            )
            if conversation is None:
                emit({"type": "error", "content": "会话不存在"})
                return
        if conversation is None:
            conversation = Conversation(user_id=user_id, title=conversation_title(payload.message))
            session.add(conversation)
            await session.flush()

        # 先取历史再落库当前用户消息：历史里不含本轮输入，否则它会和后面单独拼的 input 重复
        history = await load_history(session, conversation.id, PLAIN_HISTORY_LIMIT)
        await save_message(
            session,
            conversation.id,
            user_id,
            "user",
            payload.message,
            attachments=attachment_dicts(payload.attachments),
        )

        row = await configs.find_active()
        if row is None or not row.api_key:
            emit({"type": "error", "content": "尚未配置模型 API Key，请在「系统设置 · AI 助手配置」中填写"})
            return

        model = build_model_with_fallback(await configs.spec())
        prompt = ChatPromptTemplate.from_messages(
            [("system", row.system_prompt), *_history_tuples(history), ("human", "{input}")]
        )
        logger.info("[Chat] model=%s base_url=%s", row.model_name, row.base_url)

        content = ""
        try:
            async for chunk in (prompt | model).astream({"input": payload.message}):
                token = model_text(chunk)
                if not token:
                    continue
                content += token
                emit({"type": "token", "content": token})
        except Exception as exc:
            logger.warning("[Chat] 流式调用失败: %s", exc)
            emit({"type": "error", "content": str(exc) or "模型调用失败"})
            return

        saved = await save_message(session, conversation.id, user_id, "assistant", content)
        if conversation.title == DEFAULT_CONVERSATION_TITLE:
            conversation.title = conversation_title(payload.message)

        emit({"type": "done", "message_id": saved.id, "conversation_id": conversation.id})


def conversation_title(message: str) -> str:
    """会话标题取首条消息前若干字：列表一行放不下，太长也认不出是哪次对话。"""
    text = message.strip()
    if len(text) <= TITLE_PREVIEW_CHARS:
        return text or DEFAULT_CONVERSATION_TITLE
    return f"{text[:TITLE_PREVIEW_CHARS]}..."


async def resolve_conversation(
    session: AsyncSession,
    user_id: int,
    conversation_id: int | None,
    *,
    first_message: str,
    lesson_plan_id: int | None = None,
) -> Conversation:
    """Agent 入口的会话归属：给了编号但查不到就新建，而不是报错——
    前端在教案页唤起助手时带的可能是上一个进程留下的编号。
    """
    conversation = None
    if conversation_id:
        conversation = await session.scalar(
            select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        )
    if conversation is None:
        conversation = Conversation(
            user_id=user_id, title=conversation_title(first_message), lesson_plan_id=lesson_plan_id
        )
        session.add(conversation)
        await session.flush()
        return conversation
    # 归属只在未标记时补写：后续换了教案上下文不能把已有会话的归属改走
    if conversation.lesson_plan_id is None and lesson_plan_id:
        conversation.lesson_plan_id = lesson_plan_id
        await session.flush()
    return conversation


async def save_message(
    session: AsyncSession,
    conversation_id: int,
    user_id: int,
    role: str,
    content: str,
    *,
    attachments: list[dict[str, Any]] | None = None,
    generated_files: list[dict[str, Any]] | None = None,
    steps: list[dict[str, Any]] | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        user_id=user_id,
        role=role,
        content=content,
        attachments=attachments,
        generated_files=generated_files,
        steps=steps,
    )
    session.add(message)
    await session.flush()
    return message


async def load_history(session: AsyncSession, conversation_id: int, limit: int) -> list[Message]:
    """按时间正序返回最近 `limit` 条：先倒序取够条数，再翻回来。"""
    rows = list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
    )
    return list(reversed(rows))


def attachment_dicts(attachments: list[AttachmentRef] | None) -> list[dict[str, Any]] | None:
    if not attachments:
        return None
    return [item.model_dump(mode="json", exclude_none=True) for item in attachments]


def _history_tuples(history: list[Message]) -> list[tuple[str, str]]:
    """落库记录换成 prompt 的消息元组；system 行不进上下文。"""
    out: list[tuple[str, str]] = []
    for message in history:
        if message.role == "user":
            out.append(("human", message.content))
        elif message.role == "assistant":
            out.append(("ai", message.content))
    return out


def build_history_messages(history: list[Message]) -> list[BaseMessage]:
    """Agent 用：历史直接展开成 LangChain 消息，不经过模板。"""
    return [
        HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
        for m in history
        if m.role in ("user", "assistant")
    ]


async def generated_files_of(session: AsyncSession, user_id: int) -> list[GeneratedFile]:
    rows = await session.scalars(
        select(GeneratedFile).where(GeneratedFile.user_id == user_id).order_by(GeneratedFile.id.desc())
    )
    return list(rows)


def get_chat_service(session: SessionDep, storage: StorageDep) -> ChatService:
    return ChatService(session, storage)


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
