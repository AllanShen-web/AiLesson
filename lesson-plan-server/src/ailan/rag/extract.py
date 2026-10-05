"""从资料文件里抽取纯文本。

向量化和对话附件都要先把二进制变成文字，两种场景支持同一批格式，所以只写这一份。
Word 按文档正文顺序取段落和表格（表格拍成一行行文字，模型读竖排表格没有障碍），
PDF 逐页取文字层——扫描件的图片文字层要靠 OCR，这里不做，抽不到内容时由调用方给出提示。
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from pypdf import PdfReader

logger = logging.getLogger(__name__)

DOCX_MIMETYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIMETYPE = "application/pdf"
# 这两类不是 text/ 前缀，但内容本身就是文本
TEXT_MIMETYPES = frozenset({"application/json", "application/xml"})

# PDF 抽出来常见每页尾部的分页标记和多余空白，向量化前一并清掉
_PAGE_NOISE = re.compile(r"^\s*\d+\s*/\s*\d+\s*$", re.MULTILINE)
_MANY_BLANK_LINES = re.compile(r"\n{3,}")


def extract_document_text(mimetype: str, name: str, data: bytes) -> str | None:
    """不支持的格式返回 None，由调用方决定是提示用户还是静默跳过——不是异常。"""
    mimetype = (mimetype or "").lower()
    lowered = (name or "").lower()

    if mimetype == DOCX_MIMETYPE or lowered.endswith(".docx"):
        return _docx_text(data)
    if mimetype == PDF_MIMETYPE or lowered.endswith(".pdf"):
        return _pdf_text(data)
    if mimetype.startswith("text/") or mimetype in TEXT_MIMETYPES:
        # 编码错误的字节替换成 U+FFFD：一份混合编码的旧文档不该整篇抽不出来
        return data.decode("utf-8", errors="replace")
    return None


def _docx_text(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    lines: list[str] = []
    for node in document.element.body.iterchildren():
        if node.tag == qn("w:p"):
            text = "".join(run.text or "" for run in node.iter(qn("w:t")))
            if text.strip():
                lines.append(text.strip())
        elif node.tag == qn("w:tbl"):
            lines.extend(_table_lines(node))
    return "\n".join(lines)


def _table_lines(table: Any) -> list[str]:
    rows: list[str] = []
    for tr in table.findall(qn("w:tr")):
        cells = [
            " ".join(node.text or "" for node in tc.iter(qn("w:t"))).strip() for tc in tr.findall(qn("w:tc"))
        ]
        line = " | ".join(cell for cell in cells if cell)
        if line:
            rows.append(line)
    return rows


def _pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        logger.warning("PDF 解析失败：%s", exc)
        return ""
    text = "\n".join(_MANY_BLANK_LINES.sub("\n\n", _PAGE_NOISE.sub("", page)).strip() for page in pages)
    return _MANY_BLANK_LINES.sub("\n\n", text).strip()
