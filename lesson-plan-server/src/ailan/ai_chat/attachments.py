"""把用户上传的附件组装成一条模型消息。

图片不直接进消息：整轮先用视觉模型解析一次，之后 agent 每一步只带它产出的文字。
原做法是每一步 ReAct 都重发全部图片，5 张图能让一次生成背上几十 MB 的请求体，
而模型只需要看一次。文档类抽纯文本拼进正文，超长时显式告知模型"内容被截断"，
否则它会把截断后的片段当成全文作答。

不支持的格式与读取失败都要留一行告知模型——静默跳过等于让模型以为自己看到了这个附件。
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.ai_chat.schemas import AttachmentRef
from ailan.core.errors import AppError
from ailan.rag.extract import extract_document_text
from ailan.resources.files import read_bytes
from ailan.resources.models import Resource
from ailan.storage.service import StorageService

logger = logging.getLogger(__name__)

# 单个文档进入模型上下文的最大字符数
DOC_CHAR_LIMIT = 8_000
# 视觉模型普遍支持的图片格式，其余（svg/heic/tiff 等）不送入模型，避免整个请求 4xx
VISION_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"})
# 单张图片原始字节上限（base64 后约 5.3MB，兼容主流视觉服务的单图限制）
IMAGE_BYTE_LIMIT = 4 * 1024 * 1024
# 一次请求最多解析几张图：整轮只调一次视觉模型，太多会把这一次拖成长尾
ATTACHMENT_MAX = 10


def _truncate_for_model(text: str) -> str:
    if len(text) <= DOC_CHAR_LIMIT:
        return text
    return (
        f"{text[:DOC_CHAR_LIMIT]}\n"
        f"（注意：文档已截断，原文共 {len(text)} 字，此处仅包含前 {DOC_CHAR_LIMIT} 字）"
    )


def _vision_prompt(names: list[str]) -> str:
    listing = "、".join(f"第{i + 1}张《{name}》" for i, name in enumerate(names))
    return "\n".join(
        [
            f"上面是 {len(names)} 张图片，按顺序分别是：{listing}。",
            "请把每张图片转成文字，逐张输出，每张单独一段并以「第N张《文件名》：」开头。要求：",
            "1. 逐字转录图中出现的文字（标题、正文、表格、图注、页码、题干），表格用竖线分列；",
            "2. 再用 2~4 句说明图中展示的实物、动作步骤或版面结构；",
            "3. 只写确实看到的内容，看不清的地方标注「（看不清）」，不要臆测补全；",
            "4. 不要改写成教案，不要加评价或建议。",
        ]
    )


def _plain_text(content: Any) -> str:
    """视觉模型可能返回分段数组，统一压成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part if isinstance(part, str) else str(part.get("text") or "") for part in content]
        return "\n".join(parts).strip()
    return "" if content is None else str(content)


async def _describe_images(images: list[tuple[str, str, bytes]], model: BaseChatModel) -> str:
    """本轮图片 → 一段识别文字。整轮只调一次视觉模型。"""
    # 消息内容按 langchain 的多模态分块给出：文本块与 image_url 块混在同一个列表里
    blocks: list[str | dict[str, Any]] = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mimetype};base64,{base64.b64encode(data).decode()}"},
        }
        for _, mimetype, data in images
    ]
    blocks.append({"type": "text", "text": _vision_prompt([name for name, _, _ in images])})
    response = await model.ainvoke([HumanMessage(content=blocks)])
    return _plain_text(getattr(response, "content", ""))


async def build_user_message(
    text: str,
    attachments: list[AttachmentRef] | None,
    session: AsyncSession,
    storage: StorageService,
    vision_model: BaseChatModel,
) -> HumanMessage:
    """没有附件时原样返回，有附件时把文档正文与图片识别结果拼到消息尾部。"""
    if not attachments:
        return HumanMessage(content=text)

    body = text
    skipped: list[str] = []
    images: list[tuple[str, str, bytes]] = []

    for ref in attachments[:ATTACHMENT_MAX]:
        label = ref.name or f"附件 {ref.id}"
        try:
            resource = await session.get(Resource, ref.id)
            if resource is None:
                continue
            name = resource.original_name or resource.name or label
            label = name
            data = await read_bytes(resource, storage)
            mimetype = resource.mimetype or ref.mimetype or ""

            if mimetype.startswith("image/"):
                if mimetype not in VISION_IMAGE_MIMES:
                    skipped.append(f"{name}（不支持的图片格式 {mimetype}）")
                    logger.warning("跳过不支持的图片格式：%s %s", name, mimetype)
                    continue
                if len(data) > IMAGE_BYTE_LIMIT:
                    skipped.append(f"{name}（图片超过 4MB，未送入模型）")
                    logger.warning("跳过超大图片：%s %s 字节", name, len(data))
                    continue
                images.append((name, mimetype, data))
                continue

            extracted = extract_document_text(mimetype, name, data)
            if extracted:
                body += f"\n\n【附件：{name}】\n{_truncate_for_model(extracted)}"
                logger.info("附件已抽取文本：%s %s 字", name, len(extracted))
        except (AppError, OSError, binascii.Error, UnicodeDecodeError) as exc:
            # 读取失败也必须告知模型，否则它会以为自己看到了这个附件并凭空作答
            skipped.append(f"{label}（内容读取失败：{exc}）")
            logger.warning("附件处理失败 id=%s %s: %s", ref.id, label, exc)

    if images:
        try:
            described = await _describe_images(images, vision_model)
            if described:
                body += f"\n\n【以下图片已识别为文字，原图未提供给你】\n{described}"
                logger.info("图片已识别 %s 张 %s 字", len(images), len(described))
            else:
                skipped.append(f"{'、'.join(name for name, _, _ in images)}（图片识别没有返回内容）")
        except Exception as exc:
            skipped.append(f"{'、'.join(name for name, _, _ in images)}（图片识别失败：{exc}）")
            logger.warning("图片识别失败: %s", exc)

    if skipped:
        body += "\n\n【注意：以下附件未能提供给模型阅读】\n" + "\n".join(f"- {item}" for item in skipped)

    return HumanMessage(content=body)
