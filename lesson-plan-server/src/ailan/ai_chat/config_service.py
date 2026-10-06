"""助手配置服务：读写那行运行时配置，并按它构建模型。

模型参数存在表里而不是 `.env`：换模型、改提示词是日常操作，不该要求重启服务。
密钥只在服务端使用，管理页读到的永远是掩码。
"""

from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.ai_chat.models import DEFAULT_SYSTEM_PROMPT, AssistantConfig
from ailan.ai_chat.schemas import (
    MASK_PREFIX,
    AssistantConfigUpdate,
    AssistantConfigView,
    ConnectionTestResult,
)
from ailan.core.db import SessionDep
from ailan.core.errors import UnavailableError
from ailan.llm.factory import ModelSpec, build_model_with_fallback
from ailan.llm.text import model_text
from ailan.storage.service import StorageDep, StorageService

logger = logging.getLogger(__name__)

# 每次对话、每次生成都要取配置；一行数据读一次库不贵，但一回合能取十几次
SPEC_CACHE_TTL_SECONDS = 15
# 连通性测试只要证明"能通"，50 个 token 足够，别为一次点击烧掉长文预算
CONNECTION_TEST_MAX_TOKENS = 50
CONNECTION_TEST_PROMPT = "你好，请回复一个字：好"

# 默认值里的地址是阿里云兼容端点；密钥一律留空，由管理页填写
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL_NAME = "qwen-plus"
DEFAULT_EMBEDDING_MODEL_NAME = "text-embedding-v3"

# 缓存的是配置快照而不是会话级对象：服务实例每个请求新建一个，挂在它身上等于没缓存
_spec_cache: tuple[float, ModelSpec] | None = None


def clear_spec_cache() -> None:
    """改过配置就失效：缓存里那份没有新密钥，下一次调用会重新读库。"""
    global _spec_cache
    _spec_cache = None


class AssistantConfigService:
    def __init__(self, session: AsyncSession, storage: StorageService) -> None:
        self.session = session
        self.storage = storage

    async def ensure_default_row(self) -> None:
        """第一次启动时插入一行默认配置，管理页才有东西可改。"""
        if await self.session.scalar(select(AssistantConfig.id).limit(1)) is not None:
            return
        self.session.add(
            AssistantConfig(
                id=1,
                provider="aliyun",
                model_name=DEFAULT_MODEL_NAME,
                embedding_model_name=DEFAULT_EMBEDDING_MODEL_NAME,
                base_url=DEFAULT_BASE_URL,
                system_prompt=DEFAULT_SYSTEM_PROMPT,
                is_active=True,
            )
        )
        await self.session.flush()
        logger.info("已初始化默认 AI 助手配置（API Key 待管理页填写）")

    async def find_active(self) -> AssistantConfig | None:
        """取启用中的那一行；一行都没启用时退回第一行。

        退回而不是报"未配置"：管理员把开关关掉只想停用模型，配置内容还在，
        管理页要能把它读出来改回去。
        """
        row = await self.session.scalar(select(AssistantConfig).where(AssistantConfig.is_active).limit(1))
        if row is not None:
            return row
        return await self.session.scalar(select(AssistantConfig).order_by(AssistantConfig.id).limit(1))

    async def spec(self) -> ModelSpec:
        """给模型工厂用的配置快照，带 15 秒缓存。

        缓存的是快照而不是 ORM 行：行在会话结束后就与库断开，
        跨请求拿着它读列会在某个时刻抛 MissingGreenlet。
        """
        global _spec_cache
        cached = _spec_cache
        if cached is not None and time.monotonic() - cached[0] < SPEC_CACHE_TTL_SECONDS:
            return cached[1]

        row = await self.find_active()
        if row is None:
            raise UnavailableError("尚未配置 AI 模型，请在「系统设置 · AI 助手配置」中填写")
        spec = _to_spec(row)
        _spec_cache = (time.monotonic(), spec)
        return spec

    async def spec_or_throw(self) -> ModelSpec:
        """要真正发起调用的地方用它：没配模型就报错，而不是悄悄换成兜底模型。"""
        spec = await self.spec()
        if not spec.is_configured:
            raise UnavailableError("尚未配置模型 API Key，请在「系统设置 · AI 助手配置」中填写")
        return spec

    async def view_for_admin(self) -> AssistantConfigView | None:
        row = await self.find_active()
        return _to_view(row) if row is not None else None

    async def update(self, payload: AssistantConfigUpdate) -> AssistantConfigView:
        row = await self.find_active()
        if row is None:
            row = AssistantConfig(id=1)
            self.session.add(row)

        for field, value in payload.model_dump(exclude_unset=True).items():
            # 掩码 = "没动这一栏"；空串不在其列，向量 Key 清空是"回退用聊天 Key"的正规操作
            is_secret = field in ("api_key", "embedding_api_key")
            if is_secret and isinstance(value, str) and value.startswith(MASK_PREFIX):
                continue
            setattr(row, field, value)

        await self.session.flush()
        clear_spec_cache()
        return _to_view(row)

    async def set_avatar(self, data: bytes, mimetype: str) -> None:
        row = await self.find_active()
        if row is None:
            row = AssistantConfig(id=1)
            self.session.add(row)

        previous_key = row.avatar_key
        key = self.storage.new_key("avatar", mimetype)
        await self.storage.put(key, data, mimetype)
        row.avatar_key = key
        row.avatar_mimetype = mimetype
        # 对象存储上线前的历史行才存字节，既然要放对象存储了就一并清掉
        row.avatar_data = None
        await self.session.flush()
        # 先落库再删旧对象：反过来一旦保存失败，新旧头像会一起丢
        if previous_key:
            await self.storage.remove(previous_key)

    async def avatar(self) -> tuple[bytes, str] | None:
        """助手头像的字节与 mimetype；从未设置过返回 None，由路由回落成默认图。"""
        row = await self.find_active()
        if row is None:
            return None
        if row.avatar_key:
            if not self.storage.configured:
                return None
            return await self.storage.read(row.avatar_key), row.avatar_mimetype or "image/png"
        if row.avatar_data:
            return bytes(row.avatar_data), row.avatar_mimetype or "image/png"
        return None

    async def test_connection(self) -> ConnectionTestResult:
        """连通性测试走与生产同一条建模路径：只是限 50 token、不开流式。"""
        try:
            spec = await self.spec_or_throw()
            model = build_model_with_fallback(spec, streaming=False, max_tokens=CONNECTION_TEST_MAX_TOKENS)
            response = await model.ainvoke(CONNECTION_TEST_PROMPT)
        except Exception as exc:
            logger.warning("AI 连通性测试失败: %s", exc)
            return ConnectionTestResult(success=False, message=f"连接失败: {exc}")
        return ConnectionTestResult(success=True, message=f"模型响应: {model_text(response).strip()}")


def _to_spec(row: AssistantConfig) -> ModelSpec:
    return ModelSpec(
        provider=row.provider,
        model_name=row.model_name,
        api_key=row.api_key,
        base_url=row.base_url,
        max_tokens=row.max_tokens,
        temperature=float(row.temperature),
        enable_thinking=row.enable_thinking,
        embedding_model_name=row.embedding_model_name,
        embedding_base_url=row.embedding_base_url,
        embedding_api_key=row.embedding_api_key,
    )


def _mask(secret: str) -> str:
    return f"{MASK_PREFIX}{secret[-6:]}" if secret else ""


def _to_view(row: AssistantConfig) -> AssistantConfigView:
    """剥掉二进制与对象 key，只留"填没填"的信号——桶是匿名可读的，key 泄漏等于文件泄漏。"""
    return AssistantConfigView(
        id=row.id,
        provider=row.provider,
        model_name=row.model_name,
        embedding_model_name=row.embedding_model_name,
        embedding_base_url=row.embedding_base_url,
        base_url=row.base_url,
        api_key=_mask(row.api_key),
        embedding_api_key=_mask(row.embedding_api_key),
        max_tokens=row.max_tokens,
        temperature=float(row.temperature),
        is_active=row.is_active,
        system_prompt=row.system_prompt,
        enable_thinking=row.enable_thinking,
        has_avatar=bool(row.avatar_key or row.avatar_data),
        updated_at=row.updated_at,
    )


def get_assistant_config_service(session: SessionDep, storage: StorageDep) -> AssistantConfigService:
    return AssistantConfigService(session, storage)


AssistantConfigServiceDep = Annotated[AssistantConfigService, Depends(get_assistant_config_service)]
