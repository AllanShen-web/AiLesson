"""模板表。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ailan.core.db import Base, TimestampMixin


def empty_schema() -> dict[str, Any]:
    return {"fields": []}


class Template(Base, TimestampMixin):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # 不建外键：官方模板不属于任何人，用 user_id = 0 表示"系统所有"，
    # 建了 FK 就得为它造一个假用户，而所有校验分支本来就先被 is_official 挡住
    user_id: Mapped[int] = mapped_column(index=True)
    # 列表页每行都要显示创建者，冗余一份名字，省掉一次 join 和一遍空值处理
    creator_name: Mapped[str] = mapped_column(String(20))
    # 官方模板：进"模板市场"，人人可用
    is_official: Mapped[bool] = mapped_column(default=False)
    is_public: Mapped[bool] = mapped_column(default=False)
    # {"fields": [FormField]}，结构见 `schemas.FormField`；库内只当 JSON 存，不建列
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, default=empty_schema)
