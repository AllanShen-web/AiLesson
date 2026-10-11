"""教案导出文件的排版内核：把模板字段算成表格行与单元格规格。

Word 与 PDF 都只消费这一份规格（`build_table_cell_specs` 的输出），
所以栏宽、折行、竖排标签这些口径改一处就两种格式一起生效，不会走偏。

排版的基准是教育局下发的纸质模板：A4 纵向、页边距 1.27cm、表宽 8140 缇且整表水平居中，
表格里 24 栏网格对应模板编辑页的十二档宽度（1/12 递增）。宽度、标签位置、行高、纵向贯穿
这几个参数都来自模板的字段快照，快照必须留在教案里而不是回查模板——模板可以改、可以删，
已经写好的教案不能跟着变样。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from ailan.ai_chat.files.rich_text import (
    InlineRun,
    append_run,
    field_value_to_paragraphs,
    field_value_to_text,
    to_text,
)
from ailan.templates.schemas import default_width, normalize_width

LabelPosition = Literal["left", "top", "none"]

# 分隔符只负责切表，本身不是一格
NON_CONTENT_TYPES = frozenset({"label", "divider"})

# A4 纵向页面尺寸（twips）。Word 默认 Letter，这里显式指定 A4，
# 否则 Word 与预览端按不同纸张算页边距，表格宽度会失真。
PAGE_WIDTH = 11906
PAGE_HEIGHT = 16838
# 页边距 1.27cm（720 缇）——与纸质模板原文件一致。docx 与 PDF 共用这一套常量，
# 否则表格宽度按一张纸算、却又在另一套边距的页面里排版，会出现表格贴左、右侧大片空白。
PAGE_MARGIN_LR = 720
PAGE_MARGIN_TB = 720
# 表格总宽（twips）取模板原文件的表宽：模板里的表格并不铺满页面（720 + 8140 < 11906），
# 且整表水平居中；导出用同一个宽度与对齐，纸面才和模板一模一样。
TABLE_TOTAL_WIDTH = 8140
# PDF 用的同一套边距（pt）：1 twip = 1/20 pt
PDF_MARGIN_LR = PAGE_MARGIN_LR / 20
PDF_MARGIN_TB = PAGE_MARGIN_TB / 20

# 表格栅格列数：与前端教案页的 24 栏网格一致，保证 1/12 递增的十二档宽度没有精度损失
GRID_COLUMNS = 24
GRID_COL_WIDTH = math.floor(TABLE_TOTAL_WIDTH / GRID_COLUMNS)
# 列宽取整后 24 栏的实际合计：Word 里 tblW 与 tblGrid 必须自洽
GRID_TOTAL_WIDTH = GRID_COLUMNS * GRID_COL_WIDTH
# 标签在左时的标签列宽：2 栏（≈678 缇），对齐模板原文件 635 缇的窄标签列
LABEL_SPAN = 2
# 单元格默认左右内边距（twips），折行要把它从可用宽度里扣掉
CELL_MARGIN_LR = 108

# 全角/半角字符的估算宽度（twips），11pt 字号下用于判断能否放得下一行
FULL_WIDTH_TWIPS = 220
HALF_WIDTH_TWIPS = 110

# 括号对：切分折行单元时，一对括号连同内部内容视为不可拆分的整体
_OPEN_BRACKETS = "（(【[《"
_CLOSE_BRACKETS = "))】]》"


@dataclass
class LayoutField:
    """导出用的一条字段定义，取自教案的内容快照或编辑器传来的字段清单。

    比模板接口的 `FormField` 更宽松：这里的数据可能来自模型、可能缺字段，
    非法值一律在归一化时降级，而不是报错——导出不能因为一个宽度数字而失败。
    """

    id: str
    label: str = ""
    type: str = "textarea"
    width: float | None = None
    label_position: LabelPosition | None = None
    rows: int | None = None
    row_span: int | None = None

    @classmethod
    def from_json(cls, raw: Any) -> LayoutField:
        item = raw if isinstance(raw, dict) else {}
        return cls(
            id=to_text(item.get("id")),
            label=to_text(item.get("label")),
            type=to_text(item.get("type")) or "textarea",
            width=item.get("width") if isinstance(item.get("width"), (int, float)) else None,
            label_position=item.get("label_position") or item.get("labelPosition"),
            rows=item.get("rows") if isinstance(item.get("rows"), int) else None,
            row_span=item.get("row_span") if isinstance(item.get("row_span"), int) else None,
        )

    @property
    def is_label(self) -> bool:
        """纯栏目标题：只有标题、没有内容。"""
        return self.type == "label"

    @property
    def is_divider(self) -> bool:
        """分隔符：整行断点，把字段切成多张表。"""
        return self.type == "divider"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "width": self.width,
            "label_position": self.label_position,
            "rows": self.rows,
            "row_span": self.row_span,
        }


def normalize_label_position(value: Any) -> LabelPosition:
    """标签位置只认 top 与 none，其余（含缺省、非法值）一律按左侧并排渲染。"""
    if value == "top":
        return "top"
    if value == "none":
        return "none"
    return "left"


def normalize_rows(value: Any) -> int | None:
    """高度（行）只接受 2~20 的整数，其余按未设置处理。

    未设置时不给单元格最小高度——老教案没这个字段，导出排版保持原样。
    """
    if not isinstance(value, (int, float)):
        return None
    rounded = round(value)
    return rounded if 2 <= rounded <= 20 else None


def normalize_row_span(value: Any, is_label: bool) -> int:
    """贯穿行数只认 2~12 的整数（1 等于不贯穿），且只对纯栏目标题有意义。"""
    if not is_label or not isinstance(value, (int, float)):
        return 1
    rounded = round(value)
    return rounded if 2 <= rounded <= 12 else 1


@dataclass
class FieldLayoutItem:
    """参与排版的一个栏目。"""

    id: str
    label: str
    value: str
    is_label: bool
    # 模板宽度档位：1/12 递增十二档
    width: float
    label_position: LabelPosition
    rows: int | None = None
    # 纵向贯穿几行：只有纯栏目标题可能 >1，其余恒为 1
    row_span: int = 1


@dataclass
class FieldLayoutRow:
    # 行首被上方贯穿标题占掉的宽度（0~1），本行自己的字段从剩余宽度开始排
    covered_width: float
    fields: list[FieldLayoutItem] = field(default_factory=list)


@dataclass
class _PendingSpan:
    """活跃的贯穿格：本行行首要为它预留的宽度与剩余罩住行数。"""

    width: float
    rows_left: int


def build_layout(
    fields: list[LayoutField],
    values: dict[str, Any],
    field_labels: dict[str, str] | None = None,
) -> list[FieldLayoutRow]:
    """按模板布局规则把字段分组为表格行，与前端教案编辑页的排版保持一致。

    - 所有字段一视同仁，按模板顺序 + 宽度累加换行
    - 宽度缺失时按历史口径兜底：常见基本信息栏半宽，其余全宽
    - 竖排大标题（贯穿 N 行）强制另起一行、排在行首，它向下罩住的 N-1 行行首预留它的宽度，
      后续字段自动从剩余宽度排起

    顺序完全由 `fields`（模板快照）决定，只有 `fields` 里存在的字段才会导出；
    `values` 里多出的孤儿字段按全宽逐行兜底追加，避免内容丢失。
    模板里定义了但没填的栏目原样保留：纸面模板本来就空着这些填写栏，
    Word/PDF 要按同样的行结构渲染出空格子（导出的是模板，不是内容摘要）。
    """
    labels = field_labels or {}

    def to_item(source: LayoutField, width: float) -> FieldLayoutItem:
        label = source.label or labels.get(source.id) or source.id
        return FieldLayoutItem(
            id=source.id,
            label=label,
            value=to_text(values.get(source.id)),
            is_label=source.is_label,
            width=width,
            label_position=normalize_label_position(source.label_position),
            rows=normalize_rows(source.rows),
            row_span=normalize_row_span(source.row_span, source.is_label),
        )

    rows: list[FieldLayoutRow] = []
    used_ids: set[str] = set()
    pending: list[_PendingSpan] = []

    current: list[FieldLayoutItem] = []
    covered = 0.0
    used = 0.0
    row_open = False

    def flush() -> None:
        nonlocal covered, used
        if current:
            rows.append(FieldLayoutRow(covered_width=covered, fields=list(current)))
        current.clear()
        covered = 0.0
        used = 0.0

    def open_row() -> None:
        nonlocal covered, used, row_open
        flush()
        pending[:] = [span for span in pending if span.rows_left > 0]
        for span in pending:
            covered += span.width
            span.rows_left -= 1
        used = covered
        row_open = True

    for source in fields:
        # 分隔符不是一格，它只负责把字段切成多张表（见 split_layout_fields）
        if source.is_divider:
            continue
        used_ids.add(source.id)
        width = normalize_width(source.width, default_width(source.id))
        if not row_open:
            open_row()
        item = to_item(source, width)

        # 贯穿标题必须站行首：本行已有普通填写项就另起一行；
        # 只有其他贯穿标题时接着并排往右放——原表格里多个竖排大标题就是共用同一行行首的，
        # 各起一行会在上面空出整行轨道
        only_spans = all(entry.row_span > 1 for entry in current)
        if item.row_span > 1 and not only_spans:
            open_row()
            only_spans = True
        if used + item.width > 1 and not only_spans:
            open_row()
            only_spans = True
        # 行首只有贯穿标题时，剩余宽度放不下也排进去（顶格与标题同行），收缩到剩余宽度
        if only_spans and used + item.width > 1:
            item.width = max(1 / 12, 1 - used)

        current.append(item)
        used += item.width
        if item.row_span > 1:
            pending.append(_PendingSpan(width=item.width, rows_left=item.row_span - 1))

    flush()

    # ── 兜底：fields 里没有、但 values 里有的字段，按全宽逐行追加 ──
    for key, raw in values.items():
        if key in used_ids:
            continue
        value = to_text(raw)
        if not field_value_to_text(value).strip():
            continue
        rows.append(
            FieldLayoutRow(
                covered_width=0.0,
                fields=[
                    FieldLayoutItem(
                        id=key,
                        label=labels.get(key) or key,
                        value=value,
                        is_label=False,
                        width=1.0,
                        label_position="left",
                    )
                ],
            )
        )

    return rows


@dataclass
class LayoutSegment:
    fields: list[LayoutField]
    # 该段前面那条分隔符边界是否要真换页（连续 ≥2 个分隔符）；首段恒为 False
    page_break: bool


def split_layout_fields(fields: list[LayoutField]) -> list[LayoutSegment]:
    """按分隔符把模板字段切成多段，每段渲染成一张独立的表。

    连续 ≥2 个分隔符的那条边界额外换页（Word 模板里的分页常这么表达，
    两课时的整套字段就是这样隔开的）。前导与尾随的分隔符不产生空段。
    """
    segments: list[LayoutSegment] = []
    run = 0
    for source in fields:
        if source.is_divider:
            run += 1
            continue
        if not segments:
            segments.append(LayoutSegment(fields=[source], page_break=False))
        elif run > 0:
            segments.append(LayoutSegment(fields=[source], page_break=run >= 2))
        else:
            segments[-1].fields.append(source)
        run = 0
    return segments


@dataclass
class SegmentLayout:
    rows: list[FieldLayoutRow]
    page_break: bool


def build_segments(
    fields: list[LayoutField],
    values: dict[str, Any],
    field_labels: dict[str, str] | None = None,
) -> list[SegmentLayout]:
    """分段后的表格行：docx 一段一张表、pdf 一段一次收表。

    没有分隔符时就是单段，行结构与 `build_layout` 完全一致，旧模板导出不受影响。
    每段各排一次版，竖排贯穿标题不会跨表；孤儿值（values 里有、模板 fields 里没有的 id）
    只跟着最后一段，否则每段都会把它们重复追加一遍。
    """
    segments = split_layout_fields(fields)
    if len(segments) <= 1:
        return [SegmentLayout(rows=build_layout(fields, values, field_labels), page_break=False)]

    known_ids = {item.id for segment in segments for item in segment.fields}
    result: list[SegmentLayout] = []
    for index, segment in enumerate(segments):
        own_ids = {item.id for item in segment.fields}
        is_last = index == len(segments) - 1
        segment_values = {
            key: value
            for key, value in values.items()
            if key in own_ids or (is_last and key not in known_ids)
        }
        result.append(
            SegmentLayout(
                rows=build_layout(segment.fields, segment_values, field_labels),
                page_break=segment.page_break,
            )
        )
    return result


def estimate_text_width(text: str) -> int:
    """估算文本渲染宽度（twips）：中文与全角一字一档，其余按半角。"""
    return sum(FULL_WIDTH_TWIPS if ord(char) > 0x2E80 else HALF_WIDTH_TWIPS for char in text)


def tokenize_for_wrap(text: str) -> list[str]:
    """把文本切成折线单元：单个字符，或一对完整的括号及其内容。

    括号作为整体是为了折行时不出现「（核心素」/「养）」这种被劈开的括号。
    """
    tokens: list[str] = []
    index = 0
    while index < len(text):
        open_at = _OPEN_BRACKETS.find(text[index])
        if open_at != -1:
            close_at = text.find(_CLOSE_BRACKETS[open_at], index + 1)
            if close_at != -1:
                tokens.append(text[index : close_at + 1])
                index = close_at + 1
                continue
        tokens.append(text[index])
        index += 1
    return tokens


def wrap_runs_to_lines(paragraphs: list[list[InlineRun]], max_width: int) -> list[list[InlineRun]]:
    """按可用宽度折行样式片段，括号不被拆到两行。

    折行只切片段内部的文字，样式随文字落到每一行上，所以 Word/PDF 拿到的一定是干净的文本加标记。
    """
    lines: list[list[InlineRun]] = []
    for runs in paragraphs:
        line: list[InlineRun] = []
        line_text = ""
        for run in runs:
            for token in tokenize_for_wrap(run.text):
                if line_text and estimate_text_width(line_text + token) > max_width:
                    if line:
                        lines.append(line)
                    line, line_text = [], ""
                line_text += token
                append_run(line, replace(run, text=token))
        if line:
            lines.append(line)
    return lines


def wrap_plain_text(text: str, max_width: int) -> list[list[InlineRun]]:
    """纯文本折行：标签与标题这类没有样式的内容走这里。"""
    if not text.strip():
        return []
    return wrap_runs_to_lines([[InlineRun(text=text)]], max_width)


def span_of(width: float) -> int:
    """字段占的栅格数：width × 24，四舍五入且至少一栏。"""
    return max(1, round(width * GRID_COLUMNS))


@dataclass
class TableCellSpec:
    """一个表格单元格的几何与内容描述，docx 与 PDF 共用，保证两种格式排版一致。"""

    span: int
    width: int
    # 折行后的一行行内片段；docx 一段一个段落，PDF 一行一次绘制
    lines: list[list[InlineRun]]
    # 标签单元格加粗居中，内容单元格普通左对齐
    is_label: bool
    # 标签在上时本单元格顶部的标签行：与标签单元格一样加粗居中，其后紧跟 lines 作为正文
    label_lines: list[list[InlineRun]] | None = None
    # 正文最少占几行（模板里的「高度（行）」），不足时留白；未设置就按内容自适应
    min_lines: int | None = None
    # 纵向贯穿几行（仅竖排栏目标题 >1）
    row_span: int | None = None
    # 窄列标签竖排：docx 用单元格的文字方向属性，PDF 转坐标系后画一行
    vertical_text: bool = False


@dataclass
class TableRowSpec:
    # 行首被上方贯穿格占掉的栏数（24 栏制）；本行单元格从剩余栏开始
    covered_span: int
    cells: list[TableCellSpec] = field(default_factory=list)


def cell_line_count(cell: TableCellSpec) -> int:
    """单元格在纸上占几行：标签行 + 正文行，正文不足设定行数时按设定行数留白。

    PDF 据此定位与分页；docx 的行高交给 Word 自适应，只按「高度（行）」补最小行高。
    """
    label_lines = len(cell.label_lines or [])
    return label_lines + max(len(cell.lines), cell.min_lines or 1)


def build_table_cell_specs(title: str, layout: list[FieldLayoutRow]) -> list[TableRowSpec]:
    """把字段布局转换成表格单元格规格。

    标签在左：一个字段拆成「标签格 + 内容格」两格并排。
    标签在上：一个字段只占一格，标签作为该格顶部的标签行，正文在下方——不合并单元格，
      混排两种标签位置时各字段仍按自己的宽度并排，与页面一致。
    无标签：一个字段只占一格，格内只有正文，标签文字不出现。
    """
    specs: list[TableRowSpec] = [
        TableRowSpec(
            covered_span=0,
            cells=[
                TableCellSpec(
                    span=GRID_COLUMNS,
                    width=TABLE_TOTAL_WIDTH,
                    lines=[[InlineRun(text=title or "教案")]],
                    is_label=True,
                )
            ],
        )
    ]

    for row in layout:
        cells: list[TableCellSpec] = []
        for item in row.fields:
            field_span = span_of(item.width)
            cell_width = field_span * GRID_COL_WIDTH
            content_width = cell_width - CELL_MARGIN_LR * 2

            # 纯栏目标题：整格只有加粗居中的标题，不占内容列
            if item.is_label:
                # 折行口径：每 1/12 栏放一个全角字。1/12 宽的窄栏正好一字一行（竖排标题），
                # 全宽则一行 12 字——横排的长标题才不会一整行戳破右栏
                label_width = max(FULL_WIDTH_TWIPS, field_span * FULL_WIDTH_TWIPS // 2)
                cells.append(
                    TableCellSpec(
                        span=field_span,
                        width=cell_width,
                        lines=wrap_plain_text(item.label, label_width),
                        is_label=True,
                        min_lines=None if item.row_span > 1 else item.rows,
                        row_span=item.row_span if item.row_span > 1 else None,
                        # 竖排：贯穿格与窄列长标题（模板原文件里这类标签都是竖排）
                        vertical_text=item.row_span > 1 or (field_span <= 4 and len(item.label) > 2),
                    )
                )
                continue

            # 标签在上与无标签都是「一格里放正文」，差别只在要不要顶部的标签行
            if item.label_position in ("top", "none"):
                content_lines = wrap_runs_to_lines(field_value_to_paragraphs(item.value), content_width)
                cells.append(
                    TableCellSpec(
                        span=field_span,
                        width=cell_width,
                        lines=content_lines or [[InlineRun(text="")]],
                        is_label=False,
                        label_lines=wrap_plain_text(item.label, content_width)
                        if item.label_position == "top"
                        else None,
                        min_lines=item.rows,
                    )
                )
                continue

            # 标签列固定占窄列（模板原文件 ≈635 缇），内容占其余栏；极窄字段退回标签在上的一格结构
            if field_span < 2:
                content_lines = wrap_runs_to_lines(field_value_to_paragraphs(item.value), content_width)
                cells.append(
                    TableCellSpec(
                        span=field_span,
                        width=cell_width,
                        lines=content_lines or [[InlineRun(text="")]],
                        is_label=False,
                        label_lines=wrap_plain_text(item.label, content_width),
                        min_lines=item.rows,
                    )
                )
                continue

            label_span = min(LABEL_SPAN, field_span - 1)
            content_span = field_span - label_span
            label_cell_width = label_span * GRID_COL_WIDTH
            content_cell_width = content_span * GRID_COL_WIDTH

            cells.append(
                TableCellSpec(
                    span=label_span,
                    width=label_cell_width,
                    lines=wrap_plain_text(item.label, label_cell_width - CELL_MARGIN_LR * 2),
                    is_label=True,
                    # 模板口径：两字标签横排（课题/课型），三字及以上窄列竖排（教学目标/重点与难点…）
                    vertical_text=len(item.label) > 2,
                )
            )
            # 内容先按 HTML/markdown 解析成带样式的段落，再按列宽折行
            content_lines = wrap_runs_to_lines(
                field_value_to_paragraphs(item.value), content_cell_width - CELL_MARGIN_LR * 2
            )
            cells.append(
                TableCellSpec(
                    span=content_span,
                    width=content_cell_width,
                    lines=content_lines or [[InlineRun(text="")]],
                    is_label=False,
                    min_lines=item.rows,
                )
            )
        specs.append(TableRowSpec(covered_span=round(row.covered_width * GRID_COLUMNS), cells=cells))

    return specs
