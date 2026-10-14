"""导入模板的两份 pptx 字节怎么取。

一条 imported 记录存两份文件：`original` 是下载站那个外壳（永不动它，每次改写都从这里重做），
`working` 是改写产物。对象存储上线前的历史行把字节直接存在库里，所以两种来源都得认。
"""

from __future__ import annotations

from ailan.core.errors import NotFoundError
from ailan.ppt.models import Presentation
from ailan.storage.service import StorageService


async def shell_bytes(ppt: Presentation, storage: StorageService) -> bytes:
    """模板外壳；缺失即数据异常，读取方按 404 处理。"""
    if ppt.original_key:
        return await storage.read(ppt.original_key)
    if ppt.original_file:
        return bytes(ppt.original_file)
    raise NotFoundError("模板原文件缺失")


async def product_bytes(ppt: Presentation, storage: StorageService) -> bytes | None:
    """改写产物；还没改写时返回 None 是合法状态，由调用方决定回落到外壳还是报缺。"""
    if ppt.working_key:
        return await storage.read(ppt.working_key)
    return bytes(ppt.working_file) if ppt.working_file else None
