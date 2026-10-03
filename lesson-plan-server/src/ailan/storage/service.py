"""MinIO/S3 客户端封装。

对外只暴露四个动作（写入、整份读回、流式读、删除），业务模块不认识 boto3 的请求对象，
将来换供应商只改这一个文件。

调用一律经 `run_in_threadpool` 转出去：boto3 是同步库，直接在事件循环里发请求会把
整个进程卡在传输上——大附件动辄几十 MB，这不是"稍慢"而是会拖垮所有并发请求。
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from secrets import token_hex
from typing import Annotated, Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends
from starlette.concurrency import run_in_threadpool

from ailan.core.config import settings
from ailan.core.errors import UnavailableError

logger = logging.getLogger(__name__)

# 只有 .pdf / .docx 这类"点 + 少量字母数字"的后缀才进 key，其余一律按 mimetype 推
_SAFE_EXTENSION = re.compile(r"^\.[a-z0-9]{1,5}$")

MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


def _not_configured() -> UnavailableError:
    return UnavailableError(
        "对象存储未配置：请在 .env 中填写 MINIO_ENDPOINT / MINIO_BUCKET / MINIO_ACCESS_KEY / MINIO_SECRET_KEY"
    )


class StorageService:
    def __init__(self) -> None:
        self.bucket = settings.minio_bucket
        self._client = self._build_client() if settings.storage_enabled else None

    @staticmethod
    def _build_client() -> Any:
        endpoint = settings.minio_endpoint.rstrip("/")
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            region_name=settings.minio_region or "us-east-1",
            # MinIO 的 vhost 寻址要改本机 hosts，容器和浏览器都不可控，一律 path-style
            config=BotoConfig(s3={"addressing_style": "path"}),
        )

    @property
    def configured(self) -> bool:
        """未配置时写路径抛错、读路径只回落到历史 bytea，本地环境仍能跑起来。"""
        return self._client is not None

    def new_key(self, module: str, mimetype: str, original_name: str | None = None) -> str:
        """key 形如 `resource/<20 位随机十六进制>.docx`。

        不含任何用户输入：文件名可枚举、可注入路径，还能借 `../` 越到别人的前缀下。
        """
        return f"{module}/{token_hex(10)}{self._extension_of(mimetype, original_name)}"

    async def put(self, key: str, body: bytes, mimetype: str) -> None:
        await self._call("put_object", key, Bucket=self.bucket, Key=key, Body=body, ContentType=mimetype)

    async def read(self, key: str) -> bytes:
        """服务端解析用（向量化、附件抽取）：需要完整字节。"""
        out = await self._call("get_object", key, Bucket=self.bucket, Key=key)
        return await run_in_threadpool(out["Body"].read)

    async def open_stream(self, key: str, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
        """HTTP 响应体用：不把大文件整个读进内存。"""
        out = await self._call("get_object", key, Bucket=self.bucket, Key=key)
        stream = out["Body"]
        return _chunks_in_threadpool(stream, chunk_size)

    async def remove(self, key: str) -> None:
        """删不掉只留下 DB 已删的孤儿对象，不该让接口因此失败，所以只记日志。"""
        if self._client is None:
            return
        try:
            await self._call("delete_object", key, Bucket=self.bucket, Key=key)
        except UnavailableError as exc:
            logger.warning("删除对象 %s 失败: %s", key, exc)

    async def _call(self, method: str, key: str, **params: Any) -> Any:
        if self._client is None:
            raise _not_configured()
        try:
            return await run_in_threadpool(getattr(self._client, method), **params)
        except (BotoCoreError, ClientError) as exc:
            # 真实原因（凭证、网络、桶不存在）只进日志，返回给前端的是一句通用文案
            logger.error("对象存储操作失败 method=%s key=%s: %s", method, key, exc)
            raise UnavailableError("对象存储不可用，请检查 MinIO 服务与 MINIO_* 配置") from exc

    @staticmethod
    def _extension_of(mimetype: str, original_name: str | None) -> str:
        suffix = PurePosixPath(original_name or "").suffix.lower()
        if _SAFE_EXTENSION.match(suffix):
            return suffix
        return MIME_EXTENSIONS.get(mimetype, "")


def _chunks_in_threadpool(stream: Any, chunk_size: int) -> AsyncIterator[bytes]:
    """把同步的 StreamingBody 变成异步迭代器：每次 `read` 丢进线程池，不占事件循环。"""

    async def iterate() -> AsyncIterator[bytes]:
        while True:
            chunk = await run_in_threadpool(stream.read, chunk_size)
            if not chunk:
                break
            yield chunk

    return iterate()


storage_service = StorageService()


def get_storage() -> StorageService:
    return storage_service


StorageDep = Annotated[StorageService, Depends(get_storage)]
