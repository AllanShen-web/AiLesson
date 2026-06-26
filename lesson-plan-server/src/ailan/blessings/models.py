"""祝福表与祝福开关表。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ailan.core.db import Base, TimestampMixin, UpdatableMixin

# 默认气泡色，界面上给的是这一支粉色
_DEFAULT_COLOR = "#fd79a8"


class Blessing(Base, TimestampMixin):
    __tablename__ = "blessings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 没有外键：匿名祝福不属于任何账号，用 0 表示"没登进来的人"，
    # 挂上 users.id 会让这条最常用的人生分支直接插不进去
    user_id: Mapped[int] = mapped_column(index=True)
    nickname: Mapped[str] = mapped_column(String(20))
    # 祝福语长度由接口校验管，列本身不封顶：一屏的吉祥话不该被 255 字截掉半句
    content: Mapped[str] = mapped_column(Text)
    # 存的是 /api/images/{id} 这类相对地址，不是对象存储的 key：换存储不用改历史数据
    image_url: Mapped[str | None] = mapped_column(String(200))
    color: Mapped[str] = mapped_column(String(20), default=_DEFAULT_COLOR)
    # 管理员下架，不删数据
    hidden: Mapped[bool] = mapped_column(default=False)
    # 前端发送时先给的本地 id，撤回刚发的那条时用它对上号
    temp_id: Mapped[str | None] = mapped_column(String(50))
    target_username: Mapped[str | None] = mapped_column(String(50))


class BlessingSetting(Base, UpdatableMixin):
    """每个用户一份祝福开关：谁能在什么时候给自己送祝福。"""

    __tablename__ = "blessing_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # 总开关：关掉以后别人的送祝福入口对自己一律不展示，配置本身还留着
    enabled: Mapped[bool] = mapped_column(default=True)
