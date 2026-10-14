"""课件表：一份课件 = 一份幻灯片文档 + 可选的外部模板外壳。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ailan.core.db import Base, UpdatableMixin


def empty_doc() -> dict[str, Any]:
    return {"title": "", "theme": {}, "slides": []}


class Presentation(Base, UpdatableMixin):
    __tablename__ = "presentations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(20))
    grade: Mapped[str | None] = mapped_column(String(20))
    # 来源教案；NULL 表示不是从教案生成的
    lesson_plan_id: Mapped[int | None] = mapped_column(ForeignKey("lesson_plans.id"), index=True)
    # outline = 只有大纲；done = 页面内容已生成
    status: Mapped[str] = mapped_column(String(20), default="outline")
    # 大纲阶段产物；done 后仍保留，改页时要用它对照原规划
    outline: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 幻灯片文档（主题 + 页面数组），编辑器与导出器的唯一数据源，形状见 `schemas.PresentationDoc`
    doc: Mapped[dict[str, Any]] = mapped_column(JSONB, default=empty_doc)
    # generated = 从零生成（AI 或手动）；imported = 导入外部模板后改写
    source: Mapped[str] = mapped_column(String(12), default="generated")
    # 以下四列只对 imported 有意义：外部模板是"外壳"，改写结果另存一份，原文件保持不动
    original_name: Mapped[str | None] = mapped_column(String(255))
    # 对象存储上线前的历史行才有值
    original_file: Mapped[bytes | None] = mapped_column(LargeBinary)
    working_file: Mapped[bytes | None] = mapped_column(LargeBinary)
    original_key: Mapped[str | None] = mapped_column(String(200))
    working_key: Mapped[str | None] = mapped_column(String(200))
    # 解析模板得到的结构（每页文本槽/图片槽/位置/备注），AI 改写页面的依据
    structure: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    is_public: Mapped[bool] = mapped_column(default=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    creator_name: Mapped[str] = mapped_column(String(20))

    @property
    def has_working_file(self) -> bool:
        """列表页的「已改写」标记：还没改写时 NULL 是合法状态，不算错。"""
        return bool(self.working_key or self.working_file)
