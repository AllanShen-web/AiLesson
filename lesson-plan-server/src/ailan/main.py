"""应用装配：中间件、异常处理与全部模块路由。

起服务用 `python -m ailan.main`（端口只有 `PORT` 一个来源）；要热重载就换
`uvicorn ailan.main:app --reload --port 6586`，端口得在命令行里再给一次。
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ailan.ai_chat.router import router as ai_chat_router
from ailan.auth.router import router as auth_router
from ailan.blessings.router import router as blessings_router
from ailan.blessings.router import settings_router as blessing_settings_router
from ailan.core.config import settings
from ailan.core.db import SessionFactory, engine
from ailan.core.envelope import register_exception_handlers
from ailan.core.middleware import AccessLogMiddleware, BodySizeLimitMiddleware
from ailan.core.permissions import catalog_entries_off_menus
from ailan.images.router import router as images_router
from ailan.lesson_plans.router import router as lesson_plans_router
from ailan.menus.router import router as menus_router
from ailan.ppt.router import router as ppt_router
from ailan.rag.router import router as rag_router
from ailan.resources.router import router as resources_router
from ailan.roles.router import router as roles_router
from ailan.system.router import router as system_router
from ailan.templates.router import router as templates_router
from ailan.users.router import router as users_router

logger = logging.getLogger("ailan")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Ailan 后端已启动 端口=%s 前缀=/%s", settings.port, settings.api_prefix)
    await _seed_builtin_rows()
    await _recover_vectorizations()
    await _check_permission_catalog()
    try:
        yield
    finally:
        await engine.dispose()


async def _seed_builtin_rows() -> None:
    """补齐两行"没有就跑不起来"的内置数据：官方教案模板和 AI 助手配置。

    两者都是幂等的——已有就只对齐官方模板的结构，不覆盖用户改过的配置。
    表还没建（首次部署先跑 `alembic upgrade head` 再起服务）时只报一行，不挡启动：
    启动失败会让健康检查一直重启容器，日志里反而看不到真正的原因。
    """
    from ailan.ai_chat.config_service import AssistantConfigService
    from ailan.storage.service import get_storage
    from ailan.templates.service import TemplateService

    try:
        async with SessionFactory() as session:
            await TemplateService(session).ensure_official_template()
            await AssistantConfigService(session, get_storage()).ensure_default_row()
            await session.commit()
    except Exception as exc:
        logger.warning("内置数据初始化跳过：%s", exc)


async def _recover_vectorizations() -> None:
    """向量化任务在内存里排队，进程重启就没人再跑它们。

    库里那些行会永远停在 processing，前端的进度条一直转——启动时统一置为失败，
    用户看得见，也能重新发起。
    """
    from ailan.rag.service import reset_interrupted_tasks

    try:
        await reset_interrupted_tasks()
    except Exception as exc:
        logger.warning("向量化任务收尾跳过：%s", exc)


async def _check_permission_catalog() -> None:
    """启动自检：权限码挂在了库里不存在的菜单上，普通角色勾什么菜单都授权不上，
    而且界面上一个字都不会报，所以每次启动主动对一遍。
    """
    from sqlalchemy import select

    from ailan.menus.models import Menu

    try:
        async with SessionFactory() as session:
            paths = list(await session.scalars(select(Menu.path)))
    except Exception as exc:
        logger.warning("权限目录自检跳过（读取菜单失败）：%s", exc)
        return

    orphans = catalog_entries_off_menus(paths)
    for entry in orphans:
        logger.warning(
            "权限码 %s 挂在菜单 %s 上，但库里没有这个菜单：只有超级角色能拿到它",
            entry.code,
            entry.menu_path,
        )


def create_app() -> FastAPI:
    _configure_logging()

    app = FastAPI(
        title="Ailan 教学平台后端",
        description="教案、课件、AI 助手与知识库服务",
        version="0.1.0",
        docs_url=f"/{settings.api_prefix}/docs",
        redoc_url=None,
        openapi_url=f"/{settings.api_prefix}/openapi.json",
        lifespan=lifespan,
    )

    _configure_middlewares(app)
    _configure_cors(app)

    register_exception_handlers(app)

    prefix = f"/{settings.api_prefix}"
    app.include_router(system_router, prefix=prefix)
    app.include_router(auth_router, prefix=prefix)
    app.include_router(images_router, prefix=prefix)
    app.include_router(users_router, prefix=prefix)
    app.include_router(roles_router, prefix=prefix)
    app.include_router(menus_router, prefix=prefix)
    app.include_router(templates_router, prefix=prefix)
    app.include_router(lesson_plans_router, prefix=prefix)
    app.include_router(resources_router, prefix=prefix)
    app.include_router(ppt_router, prefix=prefix)
    app.include_router(rag_router, prefix=prefix)
    app.include_router(ai_chat_router, prefix=prefix)
    app.include_router(blessings_router, prefix=prefix)
    app.include_router(blessing_settings_router, prefix=prefix)

    return app


def _configure_middlewares(app: FastAPI) -> None:
    # add_middleware 是往栈头插，所以后加的在外层：访问日志在外，接得住被体积上限拒掉的请求
    app.add_middleware(BodySizeLimitMiddleware, limit_bytes=settings.max_json_body_bytes)
    app.add_middleware(AccessLogMiddleware)


def _configure_cors(app: FastAPI) -> None:
    """CORS 加在最外层，保证预检和错误响应也都带跨域头。

    来源按精确串比对，写进 `CORS_ORIGINS` 即可；留空等于不开跨域，明说而不是默默放行全部。
    """
    origins = [origin for origin in settings.cors_origins if origin]
    if not origins:
        logger.warning("CORS_ORIGINS 为空，跨域请求将被全部拒绝")
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


app = create_app()


if __name__ == "__main__":
    # `python -m ailan.main`：端口只有 PORT 这一个来源，不用在启动命令里再写一遍
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
