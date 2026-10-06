"""按配置造模型：聊天模型、带兜底的调用链、向量模型。

集中一处是为了避免各调用点参数漂移——`enable_thinking` 是 DashScope 的私有参数，
必须经 `model_kwargs` 透传；Anthropic 走另一个 SDK 且没有 embeddings 端点；
超时和重试次数的口径也要和流式聊天一致，否则会出现"测试连接通过、真正生成超时"。

`ModelSpec` 是一份纯数据快照，不是 ORM 对象：会话内的行在提交后就过期了，
而配置要被缓存复用（见 `ai_chat.config_service`），所以在这里断开与表的关系。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from ailan.core.config import settings
from ailan.rag.models import EMBEDDING_DIMENSIONS

# 无 embeddings 端点的供应商：向量模型必须另填一套地址与 Key
CHAT_ONLY_PROVIDERS = frozenset({"anthropic", "claude"})


@dataclass(frozen=True)
class ModelSpec:
    provider: str = "aliyun"
    model_name: str = ""
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    enable_thinking: bool = False
    embedding_model_name: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


def build_model(
    spec: ModelSpec,
    *,
    model_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    streaming: bool = True,
    max_tokens: int | None = None,
    thinking: bool | None = None,
) -> BaseChatModel:
    """从一份快照造一个模型；`thinking=None` 表示跟随配置里的全局开关。

    结构化产出类调用（模板识别、模板改写）通常要显式关思考，把 token 留给正文。
    """
    name = model_name or spec.model_name
    key = api_key or spec.api_key
    endpoint = base_url if base_url is not None else spec.base_url
    tokens = max_tokens if max_tokens is not None else spec.max_tokens
    provider = (spec.provider or "openai").lower()

    if provider in CHAT_ONLY_PROVIDERS:
        return ChatAnthropic(
            model_name=name,
            api_key=SecretStr(key),
            base_url=endpoint or None,
            max_tokens_to_sample=tokens,
            temperature=spec.temperature,
            streaming=streaming,
            # 超时口径与 OpenAI 兼容那条分支一致：同一份配置换个供应商不该变成无限等待
            timeout=settings.llm_request_timeout_seconds,
            # 和 timeout 一样是别名参数，显式写出空值，缺省行为不变
            stop=None,
        )

    enable_thinking = spec.enable_thinking if thinking is None else thinking
    return ChatOpenAI(
        model=name,
        api_key=SecretStr(key),
        base_url=endpoint or None,
        max_completion_tokens=tokens,
        temperature=spec.temperature,
        streaming=streaming,
        # 生成 4k token 的长教案在国产端点上要 1-2 分钟，短超时会把正常生成长度掐断
        timeout=settings.llm_request_timeout_seconds,
        # 厂商私有参数只能从这里进去；不写时思考模型默认开思考，正文会被推理吃光
        model_kwargs={} if enable_thinking else {"enable_thinking": False},
    )


def build_backup_models(spec: ModelSpec, **options: Any) -> list[BaseChatModel]:
    """兜底模型：同端点换个模型名。没配就用主模型本身，等于给同一次请求多一次机会。"""
    fallback = settings.llm_fallback_model
    if fallback and fallback != spec.model_name:
        return [build_model(spec, model_name=fallback, **options)]
    return [build_model(spec, **options)]


def build_model_with_fallback(spec: ModelSpec, **options: Any) -> Runnable:
    """主模型 + 兜底模型，各自带重试。

    只用于纯对话与结构化产出。Agent 场景不要用它：工具绑定要发生在每个模型自己身上，
    兜底过去的是没绑工具的模型。
    """
    attempts = settings.llm_retry_attempts
    primary = build_model(spec, **options).with_retry(stop_after_attempt=attempts)
    backups = [
        model.with_retry(stop_after_attempt=attempts) for model in build_backup_models(spec, **options)
    ]
    return primary.with_fallbacks(backups)


def build_embeddings(spec: ModelSpec) -> OpenAIEmbeddings:
    """向量模型：地址与 Key 留空回退到聊天配置，允许聊天与嵌入分属不同供应商。"""
    if (spec.provider or "").lower() in CHAT_ONLY_PROVIDERS and not spec.embedding_base_url:
        raise ValueError(
            "聊天供应商 Anthropic 没有嵌入端点，请在配置页的向量模型区单独填写 OpenAI 兼容 API 地址与 Key"
        )
    return OpenAIEmbeddings(
        model=spec.embedding_model_name,
        api_key=SecretStr(spec.embedding_api_key or spec.api_key),
        base_url=spec.embedding_base_url or spec.base_url or None,
        # 维度与 knowledge_chunks.embedding 列宽锁死：换模型要同步改表并重建索引
        dimensions=EMBEDDING_DIMENSIONS,
        max_retries=2,
        timeout=settings.llm_request_timeout_seconds,
    )


def is_tracing_enabled() -> bool:
    """配了追踪 Key 且没显式关掉才上报：一次 Agent 跑的链路不记下来，事后没法复盘。"""
    return bool(settings.langchain_api_key) and settings.langchain_tracing_v2
