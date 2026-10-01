"""图片表。"""

from __future__ import annotations

from sqlalchemy import LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from ailan.core.db import Base, TimestampMixin


class Image(Base, TimestampMixin):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    mimetype: Mapped[str] = mapped_column(String(100))
    # 对象存储上线前的历史行才有值；新图片一律走 MinIO，这一列留空
    data: Mapped[bytes | None] = mapped_column(LargeBinary)
    storage_key: Mapped[str | None] = mapped_column(String(200))
