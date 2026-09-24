"""角色表：这一张表就是权限体系的全部存储。"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ailan.core.db import Base, TimestampMixin


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    # 对用户不可见的稳定标识，`users.role_code` 指向它
    code: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    # 已授权的菜单路径（`menus.path`），这一列就是权限的全部来源：
    # 菜单路径 → `core.permissions` 目录里该菜单携带的操作码，没有第二张授权表。
    # 用 path 不用菜单 id，因为自增 id 在迁库或重建菜单表后会变，授权会静默失效。
    # `admin` 角色隐式拥有全部菜单和全部权限码，不依赖这里的取值。
    menu_paths: Mapped[list[str]] = mapped_column(JSONB, default=list)
