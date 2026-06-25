"""进程配置：全部来自环境变量或 .env，代码里不留第二处默认值。

约定是「能改的都进配置」——端口、体积上限、超时这类曾经散在各个文件里，
换环境时只能翻代码，现在一律在 `Settings` 上集中声明。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, ClassVar

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务
    port: int = 6586
    api_prefix: str = "api"
    # RAG 向量化确认弹窗会把全文（上限 30 万字）回传，默认的请求体上限接不住
    max_json_body_bytes: int = 2 * 1024 * 1024
    # 前端开发服务器是唯一的跨域来源；令牌放在 Authorization 头，跨域不必带 Cookie
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # 数据库
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_username: str = "postgres"
    db_password: str = ""
    db_database: str = "ailan"
    # 所有业务表都放在这个 schema 下，与库里其它应用隔离
    db_schema: str = "lessonPlanAi"
    db_statements_timeout: int = 60
    db_pool_size: int = 10
    db_pool_overflow: int = 20

    # 登录令牌
    jwt_secret: str
    jwt_expires_in: int = 7 * 24 * 3600

    # 邮件验证码
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""

    # 对象存储：端点、桶、两个密钥齐全才启用，否则图片退回 bytea 存库
    minio_endpoint: str = ""
    minio_bucket: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_region: str = "us-east-1"
    minio_force_path_style: bool = True

    # AI：模型名与密钥存在 ai_assistant_config 表，进程级只留兜底模型和追踪开关
    llm_fallback_model: str = ""
    llm_request_timeout_seconds: int = 180
    llm_retry_attempts: int = 3
    # 结构化产出（模板识别、模板改写）的 token 下限：思考型模型会把预算烧在推理上，留太小只剩空正文
    llm_structured_max_tokens: int = 16384
    langchain_tracing_v2: bool = True
    langchain_api_key: str = ""
    langchain_project: str = "ailan-agent"

    # 课件预览：为空则用 <工作目录>/.ppt-preview
    ppt_preview_dir: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """允许 .env 里写成逗号分隔，不必手写 JSON 数组。"""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_username}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_database}"
        )

    @property
    def storage_enabled(self) -> bool:
        return bool(
            self.minio_endpoint and self.minio_bucket and self.minio_access_key and self.minio_secret_key
        )


@lru_cache
def get_settings() -> Settings:
    """单例取配置：`Settings()` 会读文件并解析，重复构造浪费且容易读到不同值。"""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
