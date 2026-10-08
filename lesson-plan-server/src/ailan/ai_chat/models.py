"""AI 助手相关的表。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ailan.core.db import Base, TimestampMixin, UpdatableMixin

# 助手名与默认人设，系统管理页可以改
DEFAULT_SYSTEM_PROMPT = "你是 Ailan 智慧教案平台的智慧助手布布"


class AssistantConfig(Base):
    """模型、嵌入、头像与提示词配置。

    全库只有一行（`id = 1`），由「系统管理 · AI 助手配置」读写：
    这是运行时可改的配置，不是环境变量——换模型不该要求重启服务。
    """

    __tablename__ = "ai_assistant_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), default="aliyun")
    model_name: Mapped[str] = mapped_column(String(200), default="deepseek-v3")
    # 向量模型三件套：地址与 Key 留空则回退到上面的聊天配置，允许聊天与嵌入分属不同供应商
    embedding_model_name: Mapped[str] = mapped_column(String(200), default="text-embedding-v3")
    embedding_base_url: Mapped[str] = mapped_column(String(500), default="")
    embedding_api_key: Mapped[str] = mapped_column(String(500), default="")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    # 明文存库、只回给管理页，绝不出现在任何列表或日志里
    api_key: Mapped[str] = mapped_column(String(500), default="")
    max_tokens: Mapped[int] = mapped_column(default=4096)
    temperature: Mapped[float] = mapped_column(default=0.7)
    # 停用后所有对话请求直接报错，而不是悄悄换成兜底模型
    is_active: Mapped[bool] = mapped_column(default=True)
    system_prompt: Mapped[str] = mapped_column(String(2000), default=DEFAULT_SYSTEM_PROMPT)
    # 思考模式：打开后模型先输出推理再作答，前端会把推理过程折起来展示
    enable_thinking: Mapped[bool] = mapped_column(default=False)

    # 头像：对象存储上线前的历史行才存字节，新头像一律走 MinIO
    avatar_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    avatar_key: Mapped[str | None] = mapped_column(String(200))
    avatar_mimetype: Mapped[str | None] = mapped_column(String(100))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Conversation(Base, UpdatableMixin):
    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    # 会话归属的教案：列表页的「生成文件」靠它把指令落回同一条会话，
    # 打开助手时也据此恢复该教案最近的对话
    lesson_plan_id: Mapped[int | None] = mapped_column(ForeignKey("lesson_plans.id"), index=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
        lazy="raise",
    )


class Message(Base, TimestampMixin):
    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("ai_conversations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # user / assistant / system
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    # [{id, name, mimetype, size}]：本轮带上的资料附件
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # [{file_id, file_name, format, size}]：本轮由助手生成的文件
    generated_files: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # 思考与工具调用步骤，供前端折叠面板回看。
    # kind 取值：thinking / search_plans / read_plan / search_files / read_file /
    # export / fill_field / generic；pending、failed 是过程态，落库时都该已经收敛，
    # 读出来不需要再展示"进行中"。
    steps: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    conversation: Mapped[Conversation] = relationship(back_populates="messages", lazy="raise")


class GeneratedFile(Base, TimestampMixin):
    """助手产出的文件（教案 docx、课件 pptx 等），与用户自己上传的资料分开存。"""

    __tablename__ = "ai_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    file_name: Mapped[str] = mapped_column(String(300))
    mimetype: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(default=0)
    # docx / pptx / md / pdf：下载按钮的图标和文件名后缀都看它
    format: Mapped[str] = mapped_column(String(20))
    # 对象存储上线前的历史行才有值
    data: Mapped[bytes | None] = mapped_column(LargeBinary)
    storage_key: Mapped[str | None] = mapped_column(String(200))


class ConfirmationRequest(Base, TimestampMixin):
    """工具执行前的用户确认（Human-in-the-Loop）。

    Agent 准备调用需要审批的工具时，先落一条 pending 记录并中断本轮；
    用户在前端确认或拒绝后的下一轮请求据其 id 恢复执行。
    课件大纲确认是它的一个用例，其它关键操作复用同一张表。
    """

    __tablename__ = "ai_confirmations"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(index=True)
    user_id: Mapped[int] = mapped_column(index=True)
    # 待执行的工具名
    tool_name: Mapped[str] = mapped_column(String(64))
    # 工具入参快照：批准后据此原样执行，避免"确认的内容"和"执行的内容"不是一份
    args: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # low / medium / high：卡片视觉与后续策略扩展用
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    # pending / approved / rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    # 拒绝时用户填的修改意见，回传给模型
    feedback: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
