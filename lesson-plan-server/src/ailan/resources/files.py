"""文件字节的双轨读法。

一行文件记录有两个去处：对象存储上线前的旧行把字节存在 `data` 列，之后的行只在桶里存一个
`storage_key`。向量化、对话附件、预览与下载都要处理这双轨，所以判断只写在这两个函数里，
调用方不必知道某一行到底属于哪一种。

资料表的 `Resource` 和助手产出文件表的 `GeneratedFile` 都有这两列，参数因此按结构标注，
两张表共用同一份判断。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from sqlalchemy.orm import Mapped

from ailan.core.errors import NotFoundError
from ailan.storage.service import StorageService


class FileRow(Protocol):
    """按结构认领「一行带文件的表记录」。

    两个成员写成 `Mapped[...]` 而不是 `bytes | None`：ORM 类上这两个属性就是描述符，
    协议按类级别的类型比对，写成取值类型反而对不上。
    """

    data: Mapped[bytes | None]
    storage_key: Mapped[str | None]


async def read_bytes(row: FileRow, storage: StorageService) -> bytes:
    """服务端解析用（向量化、附件抽取）：需要完整字节。"""
    if row.storage_key:
        return await storage.read(row.storage_key)
    if row.data:
        return bytes(row.data)
    raise NotFoundError("文件内容不存在")


async def open_source(row: FileRow, storage: StorageService) -> AsyncIterator[bytes]:
    """HTTP 响应体用：新数据直出对象流，历史行整份读出后一次性给出。"""
    if row.storage_key:
        return await storage.open_stream(row.storage_key)
    if row.data:
        return _one_shot(bytes(row.data))
    raise NotFoundError("文件内容不存在")


async def _one_shot(data: bytes) -> AsyncIterator[bytes]:
    yield data
