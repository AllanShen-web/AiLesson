"""栏目内容 → 带样式的行内片段。

导出的正文来自两个上游：编辑器的富文本（HTML）和助手直接写的 markdown，都带着标签。
而折行要按字符宽度切字符串——一旦先折行再解析，标签就被劈成 `<p>xxx` / `yyy</p>` 两半，
Word 与 PDF 里会原样印出代码。所以这里先把内容解析成「文字 + 样式」的片段数组，
折行只切片段里的文字，字面标签在结构上就不可能落到纸上。

"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

# 检测内容是否为 HTML（含标签）：只在真的有标签时才走 HTML 解析，避免把正文里的尖括号当标签
_HTML_TAG = re.compile(r"<[a-z][\s\S]*>", re.IGNORECASE)

# 行内 markdown：***粗斜*** / **粗** / *斜* / `代码`；分组顺序即下面的判定顺序
_MD_INLINE = re.compile(r"(\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`)")

# 会折算成样式的标签；其余标签只当排版边界处理
_INLINE_MARK_TAGS: dict[str, dict[str, bool]] = {
    "b": {"bold": True},
    "strong": {"bold": True},
    "i": {"italics": True},
    "em": {"italics": True},
    "u": {"underline": True},
    "s": {"strike": True},
    "strike": {"strike": True},
    "del": {"strike": True},
    "code": {"code": True},
    # 单元格里的标题没有独立字号体系，至少加粗，读得出层级
    "h1": {"bold": True},
    "h2": {"bold": True},
    "h3": {"bold": True},
    "h4": {"bold": True},
    "h5": {"bold": True},
    "h6": {"bold": True},
}

# 块级标签：自身不产样式，但边界处要断段
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "section",
        "article",
        "aside",
        "header",
        "footer",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "hr",
        "figure",
        "figcaption",
    }
)


@dataclass
class InlineRun:
    """一个最小样式单元：一段纯文本 + 作用于它的行内样式。"""

    text: str
    bold: bool = False
    italics: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False

    def styled_with(self, style: dict[str, bool]) -> InlineRun:
        return replace(self, **{key: True for key, value in style.items() if value})

    def same_style_as(self, other: InlineRun) -> bool:
        """样式是否一致，用于合并相邻片段（否则一个单元格会产出上万个 run）。"""
        return (
            self.bold == other.bold
            and self.italics == other.italics
            and self.underline == other.underline
            and self.strike == other.strike
            and self.code == other.code
        )


def append_run(runs: list[InlineRun], run: InlineRun) -> None:
    """尾部样式相同的片段就地合并文字：折行与解析都会产生连续同样式片段。"""
    last = runs[-1] if runs else None
    if last is not None and last.same_style_as(run):
        last.text += run.text
        return
    runs.append(run)


def extend_runs(runs: list[InlineRun], extra: list[InlineRun]) -> None:
    for run in extra:
        append_run(runs, run)


def to_text(value: Any) -> str:
    """把任意栏目值收成字符串：数字与布尔直接转，其余按 JSON 序列化。

    栏目值理论上都是字符串，但 `select`/`number` 类型和上游历史数据里什么都可能混进来，
    导出不能因此报错——内容原样落纸比类型干净更要紧。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def is_html_content(content: str) -> bool:
    return bool(_HTML_TAG.search(content or ""))


def html_to_paragraphs(html: str) -> list[list[InlineRun]]:
    """HTML → 段落数组：块级标签与 `<br>` 断段，行内标签转样式，其它标签一律去壳留字。"""
    soup = BeautifulSoup(f"<div>{html}</div>", "lxml")
    root = soup.find("div")
    if root is None:
        return []

    paragraphs: list[list[InlineRun]] = []
    current: list[InlineRun] = []

    def flush() -> None:
        # 交出去的是当前这一段本身，随后换一个空列表继续攒：
        # 清空同一个列表会把已经交出去的那一段一起抹掉
        nonlocal current
        if current:
            paragraphs.append(current)
            current = []

    def add_text(raw: str, style: dict[str, bool]) -> None:
        if not raw.strip():
            # 标签之间的空白只在段中留一个空格，跨块的缩进换行直接丢掉
            if current:
                append_run(current, InlineRun(text=" ", **style))
            return
        parts = re.split(r"[\r\n]+", raw)
        for index, part in enumerate(parts):
            if index > 0:
                flush()
            if part:
                append_run(current, InlineRun(text=part, **style))

    def walk(node: Any, style: dict[str, bool]) -> None:
        if isinstance(node, NavigableString):
            add_text(str(node), style)
            return
        if not isinstance(node, Tag):
            return

        tag = node.name
        if tag == "br":
            flush()
            return
        if tag in {"ul", "ol"}:
            for index, item in enumerate(_child_items(node, "li"), start=1):
                marker = f"{index}. " if tag == "ol" else "• "
                append_run(current, InlineRun(text=marker, **style))
                for child in list(item.children):
                    walk(child, style)
                flush()
            return

        child_style = {**style, **_INLINE_MARK_TAGS[tag]} if tag in _INLINE_MARK_TAGS else style
        for child in list(node.children):
            walk(child, child_style)
        if tag in _BLOCK_TAGS:
            flush()

    for child in list(root.children):
        walk(child, {})
    flush()
    return paragraphs


def _child_items(node: Tag, name: str) -> list[Tag]:
    return [child for child in node.children if isinstance(child, Tag) and child.name == name]


def md_inline_to_runs(text: str) -> list[InlineRun]:
    """行内 markdown（`**粗**` `*斜*` `` `代码` `` `***粗斜***`）→ 行内片段。"""
    runs: list[InlineRun] = []
    last_index = 0
    for match in _MD_INLINE.finditer(text):
        if match.start() > last_index:
            append_run(runs, InlineRun(text=text[last_index : match.start()]))
        bold_italic, bold, italic, code = match.group(2), match.group(3), match.group(4), match.group(5)
        if bold_italic:
            append_run(runs, InlineRun(text=bold_italic, bold=True, italics=True))
        elif bold:
            append_run(runs, InlineRun(text=bold, bold=True))
        elif italic:
            append_run(runs, InlineRun(text=italic, italics=True))
        elif code:
            append_run(runs, InlineRun(text=code, code=True))
        last_index = match.end()
    if last_index < len(text):
        append_run(runs, InlineRun(text=text[last_index:]))
    return runs


def field_value_to_paragraphs(raw: Any) -> list[list[InlineRun]]:
    """栏目值 → 段落数组：HTML 走标签解析，其余按 markdown/纯文本折段。"""
    value = to_text(raw)
    if not value.strip():
        return []
    if is_html_content(value):
        return html_to_paragraphs(value)
    paragraphs = [md_inline_to_runs(line.strip()) for line in re.split(r"[\r\n]+", value)]
    return [runs for runs in paragraphs if runs]


def paragraphs_to_text(paragraphs: list[list[InlineRun]]) -> str:
    return "\n".join("".join(run.text for run in runs) for runs in paragraphs)


def field_value_to_text(raw: Any) -> str:
    """栏目值 → 纯文本：HTML 去标签、块级边界还原成换行。

    非 HTML 内容原样返回，markdown 导出里仍保留 `**` 这类记号。
    """
    value = to_text(raw)
    return paragraphs_to_text(html_to_paragraphs(value)) if is_html_content(value) else value


def to_plain_text_fields(values: dict[str, Any]) -> dict[str, str]:
    """把栏目全部洗成纯文本，供 PPT / markdown / txt 这些不支持行内样式的出口。

    不洗的话这些格式会把 `<p>` 当普通文字印出来。
    """
    return {key: field_value_to_text(value) for key, value in values.items()}
