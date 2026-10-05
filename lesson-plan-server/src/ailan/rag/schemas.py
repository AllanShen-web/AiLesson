"""知识库接口的数据形状。

`distance` 是余弦距离而不是相似度：0 表示完全同向，越大越不相关，
前端只需要按阈值决定显不显示，不需要理解它和相似度的关系。
"""

from __future__ import annotations

from pydantic import Field

from ailan.core.schemas import ApiModel

MAX_TEXT_CHARS = 300_000
TOP_K_DEFAULT = 5
TOP_K_MAX = 20


class ExtractionPreview(ApiModel):
    char_count: int
    chunk_count: int
    text: str


class PreviewInput(ApiModel):
    resource_id: int


class VectorizeInput(ApiModel):
    resource_id: int
    # 确认弹窗里用户编辑过的文本；不传表示"就用现场解析出来的那份"
    text: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)


class VectorizationResult(ApiModel):
    status: str


class DocumentStatus(ApiModel):
    resource_id: int
    status: str
    chunk_count: int
    error_message: str | None = None


class RagHit(ApiModel):
    resource_id: int
    resource_name: str
    chunk_index: int
    content: str
    distance: float
