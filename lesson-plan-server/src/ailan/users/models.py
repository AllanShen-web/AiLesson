"""用户表。"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ailan.core.db import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    # 只存 bcrypt 散列；接口返回前一律经 `UserView` 过滤掉这一列
    password: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    nickname: Mapped[str | None] = mapped_column(String(255))
    # 只记角色的 code，不建外键：角色被停用或改名不应连坐删掉账号
    role_code: Mapped[str | None] = mapped_column(String(255))
    frozen: Mapped[bool] = mapped_column(default=False)
    avatar: Mapped[str | None] = mapped_column(String(255))
    # 历史头像的 /api/images/{id} 链接，前端用来做"选回上一张"
    avatar_history: Mapped[list[str]] = mapped_column(JSONB, default=list)
