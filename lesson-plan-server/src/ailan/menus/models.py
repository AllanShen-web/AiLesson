"""菜单表：前端侧边栏的数据源，同时是权限授权的最小单位。"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ailan.core.db import Base, TimestampMixin


class Menu(Base, TimestampMixin):
    __tablename__ = "menus"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    icon: Mapped[str] = mapped_column(String(255))
    # 前端路由，也是角色授权用的键：全局唯一且改路由即是改授权，不会出现两处不一致
    path: Mapped[str] = mapped_column(String(255), unique=True)
    sort: Mapped[int] = mapped_column(default=0)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("menus.id"))

    children: Mapped[list[Menu]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="Menu.sort",
    )
    parent: Mapped[Menu | None] = relationship(
        back_populates="children",
        remote_side=[id],
        single_parent=True,
    )
