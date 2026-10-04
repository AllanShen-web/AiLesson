"""资料接口的数据形状。

返回体里永远没有 `data` 与 `storage_key`：桶是匿名可读的，对象 key 一旦漏给前端，
等于把"只有上传者能下载"变成"知道链接的人都能下载"。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ailan.core.schemas import ApiModel

NAME_MAX = 100
BASIC_LABEL_MAX = 20
ORIGINAL_NAME_MAX = 200

# 上传时随文件一起提交的其他信息
UPLOAD_SIZE_LIMIT = 50 * 1024 * 1024

# 与前端 accept 一致的白名单：按前缀放行，Word/PPT/Excel 的 OOXML 类型都以同一个前缀开头
ALLOWED_MIME_PREFIXES: tuple[str, ...] = (
    "application/pdf",
    "image/",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.",
    "application/vnd.ms-powerpoint",
    "text/",
    "application/json",
    "application/xml",
)


def is_allowed_mimetype(mimetype: str) -> bool:
    return any(mimetype.startswith(prefix) or mimetype == prefix for prefix in ALLOWED_MIME_PREFIXES)


class ResourceView(ApiModel):
    id: int
    name: str
    subject: str
    grade: str
    is_public: bool
    original_name: str
    mimetype: str
    size_bytes: int
    user_id: int
    creator_name: str
    created_at: datetime


class ResourceUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    subject: str | None = Field(default=None, max_length=BASIC_LABEL_MAX)
    grade: str | None = Field(default=None, max_length=BASIC_LABEL_MAX)
    is_public: bool | None = None
