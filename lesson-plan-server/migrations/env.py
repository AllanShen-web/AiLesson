"""迁移运行环境：把 `Base.metadata` 接到库上。

连接串和 schema 都取自应用配置，`alembic.ini` 里一个都不写——服务读什么环境变量，
迁移就建什么库，不允许出现两份可能对不上的默认值。

在线跑（`alembic upgrade head`）用异步引擎，和运行时同一个驱动；`--sql` 离线跑只要方言
能把 DDL 渲染成脚本，不建连接，所以手上没库也能检查迁移写得对不对。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import ailan.models  # noqa: F401  表类只有被导入才进 metadata，漏一个 autogenerate 就看不见那张表
from ailan.core.config import settings
from ailan.core.db import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

CONTEXT_OPTIONS = {
    "target_metadata": target_metadata,
    # 业务表全在 settings.db_schema 下，比对时必须连 schema 一起反射，
    # 否则 autogenerate 看不见现存表，每次生成的都是「整库重建」
    "include_schemas": True,
    # 版本号表留在默认的 public 下：空库里业务 schema 还不存在，而 Alembic 在建表之前就要读它。
    # 跟着业务 schema 走只会让第一次 upgrade（在线和 `--sql`）都得到人工补一步，不值当。
    "version_table_schema": None,
}


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, literal_binds=True, **CONTEXT_OPTIONS)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, **CONTEXT_OPTIONS)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        # 迁移是一次性任务，不必预留连接池
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
