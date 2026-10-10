"""教案 → markdown / 纯文本。

行结构与 Word、PDF 一致（同一份字段布局），差别只在渲染手段——Word 画真表格、
PDF 描边框、这两种格式只有一行行文字。

md/txt 没有版面宽度限制，正文一律用原文，不套用按栏宽折好的行：
那套折行是给纸上的一格一格用的，用在流式文本里会把长句硬切成两半。
"""

from __future__ import annotations

from typing import Any

from ailan.ai_chat.files.layout import FieldLayoutItem, LayoutField, build_layout
from ailan.ai_chat.files.rich_text import field_value_to_text, to_plain_text_fields

# 同一行的多个字段之间用全角空格拉开距离：纯文本没有列宽，并排只能这样表达
_COLUMN_GAP = "　　"
_FIELD_GAP = "　"
_FALLBACK_RULE = "────────"


def _filled_rows(
    fields: list[LayoutField], values: dict[str, Any], labels: dict[str, str]
) -> list[list[FieldLayoutItem]]:
    """模板布局去掉空白栏：纸面格式要留空白填写栏，md/txt 不需要。"""
    rows: list[list[FieldLayoutItem]] = []
    for row in build_layout(fields, to_plain_text_fields(values), labels):
        kept = [item for item in row.fields if item.is_label or item.value.strip()]
        if kept:
            rows.append(kept)
    return rows


def _heading(title: str, markdown: bool) -> str:
    return f"# {title}\n\n" if markdown else f"{title}\n\n"


def _label_block(label: str, value: str) -> str:
    """标签独立成一块，正文在下方。"""
    return f"**{label}**\n\n{value.strip()}\n\n"


def _one_field(item: FieldLayoutItem) -> str:
    """单字段行：标签在左也只有一格可排，仍让标签独立成行。"""
    if item.is_label:
        return f"**{item.label}**\n\n"
    if item.label_position == "none":
        return f"{item.value.strip()}\n\n"
    return _label_block(item.label, item.value)


def _inline_fields(items: list[FieldLayoutItem]) -> str:
    """并排在同一行的几个「标签在左」字段：`**课题**　值　　**课型**　值`。"""
    return _COLUMN_GAP.join(f"**{item.label}**{_FIELD_GAP}{item.value.strip()}" for item in items) + "\n\n"


def _markdown_row(row: list[FieldLayoutItem]) -> str:
    if len(row) == 1:
        return _one_field(row[0])
    blocks: list[str] = []
    inline: list[FieldLayoutItem] = []

    def flush() -> None:
        # 标签在上与无标签都得独占一块，所以遇到它们先把攒着的并排字段收尾
        if inline:
            blocks.append(_inline_fields(inline))
            inline.clear()

    for item in row:
        if item.is_label:
            flush()
            blocks.append(f"**{item.label}**\n\n")
        elif item.label_position == "none":
            flush()
            blocks.append(f"{item.value.strip()}\n\n")
        elif item.label_position == "top":
            flush()
            blocks.append(_label_block(item.label, item.value))
        else:
            inline.append(item)
    flush()
    return "".join(blocks)


def _plain_row(row: list[FieldLayoutItem]) -> str:
    parts = [
        item.label
        if item.is_label
        else item.value.strip()
        if item.label_position == "none"
        else f"{item.label}:\n{item.value.strip()}"
        if item.label_position == "top"
        else f"{item.label}: {item.value.strip()}"
        for item in row
    ]
    return "\n".join(parts) + "\n\n"


def _fallback(title: str, info_line: str, content: str, markdown: bool) -> str:
    """没有可渲染的栏目时（助手只给了一段正文）：标题 + 信息行 + 分隔线 + 原文。"""
    info = f"{info_line}\n\n" if info_line else ""
    rule = "---\n\n" if markdown else f"{_FALLBACK_RULE}\n\n"
    body = f"{field_value_to_text(content)}\n" if content else ""
    return _heading(title, markdown) + info + rule + body


def generate_plain_text(
    *,
    title: str,
    fields: dict[str, Any],
    field_labels: dict[str, str],
    content: str,
    layout_fields: list[LayoutField],
    info_line: str = "",
    markdown: bool = False,
) -> bytes:
    """`markdown=True` 出 .md，否则出 .txt；两者共用同一份行结构。"""
    name = (title or "").strip() or "教案"
    rows = _filled_rows(layout_fields, fields, field_labels)
    if not rows:
        return _fallback(name, info_line, content, markdown).encode("utf-8")
    render_row = _markdown_row if markdown else _plain_row
    text = _heading(name, markdown) + "".join(render_row(row) for row in rows)
    return (text.rstrip() + "\n").encode("utf-8")
