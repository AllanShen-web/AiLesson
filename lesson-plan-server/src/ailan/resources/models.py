"""资料表。"""

from __future__ import annotations

from sqlalchemy import ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from ailan.core.db import Base, TimestampMixin


class Resource(Base, TimestampMixin):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # 界面改版后上传不再采集学科/年级，留空串是为了历史数据仍能被筛选命中
    subject: Mapped[str] = mapped_column(String(20), default="")
    grade: Mapped[str] = mapped_column(String(20), default="")
    is_public: Mapped[bool] = mapped_column(default=False)
    # 下载时用的展示名，与对象存储里那个随机 key 分开存
    original_name: Mapped[str] = mapped_column(String(200))
    mimetype: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(default=0)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    creator_name: Mapped[str] = mapped_column(String(20))
    # 对象存储上线前的历史行才有值；新文件一律走 MinIO，这一列留空
    data: Mapped[bytes | None] = mapped_column(LargeBinary)
    storage_key: Mapped[str | None] = mapped_column(String(200))
