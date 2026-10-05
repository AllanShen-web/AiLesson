"""向量化任务表与分块表。"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ailan.core.db import Base, UpdatableMixin

# 嵌入模型的输出维度，建表时的 vector(1024) 由它来；换模型要同时改这里并重建索引
EMBEDDING_DIMENSIONS = 1024


class KnowledgeDocument(Base, UpdatableMixin):
    """一份资料的向量化任务与状态（只有用户上传的文件才进知识库）。"""

    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 一份资料只有一条任务：重复点"向量化"是更新这一行，不是再插一条
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # pending / processing / ready / failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    chunk_count: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(String(500))
    # 摄取时用的嵌入模型名：换模型后旧向量与新模型不可比，靠这一列提示重新向量化
    embedding_model: Mapped[str] = mapped_column(String(200), default="")
    # 用户在确认弹窗里编辑过的文本；有值时直接向量化它，不再重新解析文件
    edited_text: Mapped[str | None] = mapped_column(Text)


class KnowledgeChunk(Base):
    """文档分块及其向量。

    冗余 `resource_id` / `user_id`：检索要按"本人 + 公开"过滤，
    带上这两列就不用为了一个 WHERE 去 join 父表——向量检索的开销大头在扫描，
    少一次 join 比少一点存储重要。
    """

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    resource_id: Mapped[int] = mapped_column(index=True)
    user_id: Mapped[int] = mapped_column(index=True)
    chunk_index: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
