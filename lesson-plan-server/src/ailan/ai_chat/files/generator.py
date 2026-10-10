"""教案导出的统一入口：认格式、备料、把排版交给各格式自己的渲染器。

上游给的格式串是模型写的，别名与大小写都不定（word / PPT / markdown），所以先归一化；
连格式都没写（回复被长度上限截掉）时默认出 Word，保证一定有文件产出——
只有明确写了不支持的格式才报错。

五种格式吃同一份料：栏目内容 + 模板字段定义（决定排版）+ 字段中文名。
排版规则都在 `layout` 里，这里只负责分发。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from ailan.ai_chat.files.docx import generate_docx
from ailan.ai_chat.files.layout import LayoutField
from ailan.ai_chat.files.pdf import generate_pdf
from ailan.ai_chat.files.plain_text import generate_plain_text
from ailan.ai_chat.files.pptx import generate_pptx

logger = logging.getLogger(__name__)

FileFormat = Literal["md", "txt", "docx", "pdf", "pptx"]

SUPPORTED = ("md", "txt", "docx", "pdf", "pptx")

FORMAT_ALIASES: dict[str, FileFormat] = {
    "md": "md",
    "markdown": "md",
    "txt": "txt",
    "text": "txt",
    "plain": "txt",
    "docx": "docx",
    "doc": "docx",
    "word": "docx",
    "pdf": "pdf",
    "pptx": "pptx",
    "ppt": "pptx",
    "powerpoint": "pptx",
}

MIMETYPES: dict[FileFormat, str] = {
    "md": "text/markdown",
    "txt": "text/plain",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# 系统内置字段 id → 中文名：上游没传字段清单、或教案快照被抹掉时兜底，
# 至少官方模板和常见 basic-* 字段能显示中文名而不是裸 id
BUILTIN_FIELD_LABELS: dict[str, str] = {
    "official-subject": "科目",
    "official-grade": "年级",
    "official-textbook": "教材版本",
    "official-title": "课题标题",
    "official-objectives": "教学目标（核心素养）",
    "official-keydifficult": "教学重难点",
    "official-preparation": "教学准备",
    "official-process": "教学过程",
    "official-boarddesign": "板书设计",
    "official-reflection": "教学反思",
    "basic-topic": "课题",
    "basic-subject": "学科",
    "basic-grade": "年级",
    "basic-unit": "单元",
    "basic-semester": "学期",
    "basic-class": "班级",
    "basic-teacher": "授课教师",
    "basic-date": "授课日期",
    "basic-duration": "课时",
    "topic": "课题",
    "subject": "学科",
    "grade": "年级",
    "unit": "单元",
    "objectives": "教学目标",
    "process": "教学过程",
    "content": "教学内容",
    "preparation": "教学准备",
    "key_points": "教学重点",
    "difficulties": "教学难点",
    "reflection": "教学反思",
    "summary": "课堂小结",
    "homework": "作业布置",
    "board_design": "板书设计",
}

# 封面信息行按这个顺序与中文名展示
FORM_INFO_LABELS = {"topic": "课题", "unit": "单元", "subject": "学科", "grade": "年级"}

_UNSAFE_FILE_CHARS = re.compile(r'[<>:"/\\|?*]')


@dataclass
class ExportedFile:
    """一个导出的成品：字节 + 下载要用的文件名与类型。"""

    content: bytes
    file_name: str
    mimetype: str
    format: FileFormat


def resolve_field_label(field_id: str, field_labels: dict[str, str] | None = None) -> str:
    """先取传入的映射，再查内置兜底，最后退回裸 id。"""
    if field_labels and field_labels.get(field_id):
        return field_labels[field_id]
    return BUILTIN_FIELD_LABELS.get(field_id) or field_id


def normalize_format(raw: Any) -> FileFormat | None:
    """大小写不敏感、支持别名；认不出来返回 None。"""
    return FORMAT_ALIASES.get(str(raw or "").strip().lower())


def normalize_fields(raw: Any) -> dict[str, Any]:
    """栏目内容可能是对象，也可能是模型传来的 JSON 字符串，统一成对象。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def safe_file_name(title: str) -> str:
    """标题当文件名用，先把 Windows/POSIX 认作分隔符与控制符的字符换成下划线。"""
    return _UNSAFE_FILE_CHARS.sub("_", title or "").strip() or "教案"


def form_info_line(form_info: dict[str, Any] | None) -> str:
    """PPT 封面与 md/txt 抬头那行「课题: …　学科: …」。"""
    entries = [
        f"{FORM_INFO_LABELS.get(key, key)}: {value}"
        for key, value in (form_info or {}).items()
        if str(value or "").strip()
    ]
    return "    ".join(entries)


def generate_file(
    *,
    format: str,
    title: str,
    fields: Any,
    field_labels: dict[str, str] | None = None,
    layout_fields: list[LayoutField] | None = None,
    content: str = "",
    form_info: dict[str, Any] | None = None,
) -> ExportedFile:
    """生成一份教案导出文件。栏目内容优先，`content`（纯文本/markdown）兜底。

    格式认不出来时抛 ValueError，调用方转成给用户看得懂的一句话。
    """
    raw_format = str(format or "").strip()
    resolved = normalize_format(raw_format)
    if resolved is None:
        if raw_format:
            raise ValueError(f"不支持的文件格式：{format}（支持 {'、'.join(SUPPORTED)}）")
        resolved = "docx"

    plan_title = (title or "").strip() or "教案"
    # Word 与 PDF 没有封面页，基本信息本来就占表格里的栏目，只有线性版面才单独排一行
    plan = {
        "title": plan_title,
        "fields": normalize_fields(fields),
        "field_labels": field_labels or {},
        "content": str(content or ""),
        "layout_fields": layout_fields or [],
    }
    info_line = form_info_line(form_info)

    if resolved == "docx":
        body = generate_docx(**plan)
    elif resolved == "pdf":
        body = generate_pdf(**plan)
    elif resolved == "pptx":
        body = generate_pptx(**plan, info_line=info_line)
    else:
        body = generate_plain_text(**plan, info_line=info_line, markdown=resolved == "md")

    logger.info("导出教案：%s（%s，%.1fKB）", plan_title, resolved, len(body) / 1024)
    return ExportedFile(
        content=body,
        file_name=f"{safe_file_name(plan_title)}.{resolved}",
        mimetype=MIMETYPES[resolved],
        format=resolved,
    )
