"""教案表。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ailan.core.db import Base, UpdatableMixin


def empty_content() -> dict[str, Any]:
    return {"fields": [], "values": {}}


class LessonPlan(Base, UpdatableMixin):
    __tablename__ = "lesson_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(20))
    grade: Mapped[str | None] = mapped_column(String(20))
    # 用 NULL 表示"不来自任何模板"：早先用 0 当哨兵值，一旦有人真建出 id=0 的模板就分不出了
    template_id: Mapped[int | None] = mapped_column(ForeignKey("templates.id"), index=True)
    # {"fields": [...], "values": {...}}：fields 是建教案当时模板结构的快照，values 是填进去的内容。
    # 快照必须留在教案里而不是回查模板——模板可以改字段、可以删，而已经写好的教案不能跟着变样，
    # 导出 Word 也全靠这份快照排版。
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, default=empty_content)
    is_public: Mapped[bool] = mapped_column(default=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    creator_name: Mapped[str] = mapped_column(String(20))
