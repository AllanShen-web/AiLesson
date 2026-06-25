"""数据库连接与会话。

表结构变更一律走 Alembic（`migrations/`），代码里没有自动同步 schema 的开关——
库和应用版本不一致时，静默改表比报错危险得多。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ailan.core.config import settings

# 约束名交给命名约定生成，迁移脚本里不会出现 `eta_1234567890` 这种回头查不到来源的名字。
# 索引按「表名 + 列名」而不是默认的 `column_0_label`：后者会把 schema 拼进去，
# 名字长到没法读，而索引本来就建在表所在的那个 schema 里，带上 schema 是冗余。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=settings.db_schema, naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """由数据库落时间，避免多实例时钟不一致。

    一律带时区：`now()` 存的是服务器时区下的绝对时刻，代码里写的又是 UTC 的
    `datetime.now(UTC)`，列不带时区就会在读写之间丢掉那层偏移。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UpdatableMixin(TimestampMixin):
    """既记创建时间也记最后修改时间；`onupdate` 由 ORM 兜住，业务代码不用记得改它。"""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_pool_overflow,
    pool_pre_ping=True,
    connect_args={
        # 慢查询在数据库侧直接截断，免得一条失控 SQL 长期占住连接池
        "server_settings": {"statement_timeout": str(settings.db_statements_timeout * 1000)}
    },
)

SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """请求级会话：正常返回时统一提交，抛异常则整体回滚。

    service 里只 `flush()` 拿自增 id，不需要到处写 commit，也不会有半途提交的数据。
    """
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]
