"""教案 → PDF。

纸面口径与 Word 完全同源：同一张 A4、同一套页边距、同一份单元格规格
（`layout.build_table_cell_specs`），缇换算成点（1pt = 20 缇）后逐格描边、逐行落字。
两种格式唯一的差别是 Word 有真表格、这里只有矩形边框，所以栏宽、折行、竖排、留白
全部取自同一份规格，改排版只改 layout 一处。

中文字体必须嵌进文件：PDF 阅读器不保证有宋体黑体，不嵌入就整页方框。
reportlab 只吃 TrueType 轮廓（.ttf / .ttc），CFF 的 .otf 会在解析 `glyf` 表时失败，
所以候选清单里没有 .otf；嵌入时按实际用到的字形裁剪，文件不会因为一个字体胀到十几 MB。
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from typing import Any

from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.pdfgen.canvas import Canvas

from ailan.ai_chat.files.layout import (
    GRID_COLUMNS,
    GRID_TOTAL_WIDTH,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    PDF_MARGIN_LR,
    PDF_MARGIN_TB,
    LayoutField,
    TableCellSpec,
    TableRowSpec,
    build_segments,
    build_table_cell_specs,
)
from ailan.ai_chat.files.rich_text import InlineRun, field_value_to_text

logger = logging.getLogger(__name__)

TWIPS_PER_POINT = 20
PAGE_W = PAGE_WIDTH / TWIPS_PER_POINT
PAGE_H = PAGE_HEIGHT / TWIPS_PER_POINT
# 与 Word 同一套页边距，表格才能落在纸面的同一个位置
LEFT = PDF_MARGIN_LR
BOTTOM = PDF_MARGIN_TB
TOP = PAGE_H - PDF_MARGIN_TB
MAX_W = PAGE_W - LEFT * 2

# 表宽取模板原文件的 8136 缇，整表水平居中（Word 里是 tblW + jc=center 同一个口径）
TABLE_W = GRID_TOTAL_WIDTH / TWIPS_PER_POINT
TABLE_X = LEFT + (MAX_W - TABLE_W) / 2

CELL_PAD_X = 5
CELL_PAD_Y = 4
LABEL_SIZE = 11
BODY_SIZE = 10.5
LABEL_LINE_H = 15
BODY_LINE_H = 14
# 两张表之间的空隙：不留的话上下表的边框会叠成一条粗线（Word 里是靠中间那个段落隔开的）
TABLE_GAP = 10
TITLE_SIZE = 18
BORDER_WIDTH = 0.5
CONTINUATION_WIDTH = 0.75

INK = colors.Color(0.15, 0.15, 0.15)
BORDER = colors.black

# 常规字面：Windows → Linux 容器 → macOS。容器镜像里只有 Noto CJK 的 .ttc
_REGULAR_CANDIDATES = (
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
)
# 黑体本身就是粗字重，找不到专门的 Bold 字面时用它顶，实在没有再退回常规字面
_BOLD_CANDIDATES = (
    r"C:\Windows\Fonts\simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
)


def _register(role: str, candidates: tuple[str, ...], fallback: str) -> str:
    """注册第一把读得到的字体，名字带 role 以免两次注册互相顶掉。"""
    for path in candidates:
        if not os.path.isfile(path):
            continue
        name = f"lesson-plan-{role}"
        if name not in pdfmetrics.getRegisteredFontNames():
            try:
                pdfmetrics.registerFont(TTFont(name, path))
            except TTFError:
                # 字体损坏或不是 TrueType 轮廓：跳过这一把继续找，功能优先于字号精确
                logger.warning("PDF 字体 %s 注册失败，改用下一个候选", path, exc_info=True)
                continue
        return name
    logger.warning("系统里没有可嵌入的中文字体，PDF 中文会缺字形")
    return fallback


@dataclass(frozen=True)
class _Fonts:
    """PDF 只有常规与粗体两套字面：斜体、下划线、代码底纹一律降级为常规文字，但内容不丢。"""

    regular: str
    bold: str

    def face_of(self, run: InlineRun, is_label: bool) -> str:
        return self.bold if is_label or run.bold else self.regular


@dataclass
class _Line:
    """要落纸的一行：行内片段 + 用标签样式还是正文样式绘制。"""

    runs: list[InlineRun]
    is_label: bool

    @property
    def height(self) -> float:
        return LABEL_LINE_H if self.is_label else BODY_LINE_H


def _lines_of(cell: TableCellSpec) -> list[_Line]:
    """单元格展开成可视行序列：标签在上的先排标签行，正文不足「高度（行）」时补空行留白。"""
    label_lines = [_Line(runs, True) for runs in cell.label_lines or []]
    body_count = max(len(cell.lines), cell.min_lines or 1)
    body = [
        _Line(cell.lines[index] if index < len(cell.lines) else [], cell.is_label)
        for index in range(body_count)
    ]
    return [*label_lines, *body]


def _cell_text(cell: TableCellSpec) -> str:
    """格子里的文字并成一句：竖排只画一行，用不上按栏宽折好的行。"""
    return "".join(run.text for line in cell.lines for run in line)


def _cell_width(cell: TableCellSpec) -> float:
    """格宽按栏数占比分配：累加值恒等于表宽，不会逐格累积舍入误差。"""
    return (cell.span / GRID_COLUMNS) * TABLE_W


def _runs_width(runs: list[InlineRun], fonts: _Fonts, size: float, is_label: bool) -> float:
    measured = (pdfmetrics.stringWidth(run.text, fonts.face_of(run, is_label), size) for run in runs)
    return sum(measured)


class _Sheet:
    """一支画笔加一个落笔游标：纵向自上而下，跨页由它统一负责。"""

    def __init__(self, canvas: Canvas, fonts: _Fonts) -> None:
        self.canvas = canvas
        self.fonts = fonts
        self.y = TOP

    def turn_page(self) -> None:
        self.canvas.showPage()
        self.y = TOP

    def room_lines(self, line_height: float) -> int:
        """当前页还剩几行可画。"""
        return max(0, int((self.y - BOTTOM) // line_height))

    def text(self, content: str, x: float, y: float, face: str, size: float) -> None:
        self.canvas.setFont(face, size)
        self.canvas.setFillColor(INK)
        self.canvas.drawString(x, y, content)

    def rectangle(self, x: float, top: float, width: float, height: float) -> None:
        self.canvas.setStrokeColor(BORDER)
        self.canvas.setLineWidth(BORDER_WIDTH)
        self.canvas.rect(x, top - height, width, height, stroke=1, fill=0)

    def horizontal_line(self, x: float, width: float, y: float) -> None:
        self.canvas.setStrokeColor(BORDER)
        self.canvas.setLineWidth(CONTINUATION_WIDTH)
        self.canvas.line(x, y, x + width, y)

    def vertical_text(self, content: str, x: float, top: float, width: float, height: float) -> None:
        """竖排：坐标系顺时针转 90° 后画一行，文字在这块格子里横竖都居中。

        Word 那边用的是单元格的文字方向属性，PDF 没有这个属性，只能转坐标系。
        """
        face = self.fonts.bold
        text_width = pdfmetrics.stringWidth(content, face, LABEL_SIZE)
        self.canvas.saveState()
        self.canvas.translate(x + width / 2, top - height / 2)
        self.canvas.rotate(-90)
        # 基线内移小半个字身：转过来之后局部纵轴才是字的可视厚度
        self.canvas.setFont(face, LABEL_SIZE)
        self.canvas.setFillColor(INK)
        self.canvas.drawString(-text_width / 2, -LABEL_SIZE * 0.35, content)
        self.canvas.restoreState()


@dataclass
class _ActiveSpan:
    """正在绘制的竖排贯穿格（row_span > 1 的栏目标题）。

    它要等罩住的每一行都画完才知道自己多高，所以先记下顶部，收尾时一次画出通高大格。
    """

    cell: TableCellSpec
    x: float
    top: float
    rows_left: int

    def finish(self, sheet: _Sheet, bottom: float) -> None:
        height = max(LABEL_LINE_H + CELL_PAD_Y * 2, self.top - bottom)
        width = _cell_width(self.cell)
        sheet.rectangle(self.x, self.top, width, height)
        label = _cell_text(self.cell)
        if label:
            sheet.vertical_text(label, self.x, self.top, width, height)


def _draw_line(sheet: _Sheet, line: _Line, x: float, width: float, baseline: float) -> None:
    """画一行文字：标签行整行居中，正文行从左边距起逐片段相接。"""
    if not line.runs:
        return
    size = LABEL_SIZE if line.is_label else BODY_SIZE
    if line.is_label:
        line_width = _runs_width(line.runs, sheet.fonts, size, True)
        cursor = x + max(CELL_PAD_X, (width - line_width) / 2)
    else:
        cursor = x + CELL_PAD_X
    for run in line.runs:
        if not run.text:
            continue
        face = sheet.fonts.face_of(run, line.is_label)
        sheet.text(run.text, cursor, baseline, face, size)
        cursor += pdfmetrics.stringWidth(run.text, face, size)


def _draw_cell_fragment(
    sheet: _Sheet,
    cell: TableCellSpec,
    lines: list[_Line],
    x: float,
    segment_top: float,
    segment_height: float,
    first_line: int,
    line_count: int,
) -> None:
    """画一格在本行这一段里的边框与文字；一行跨两页时，每页各画自己那几行。"""
    width = _cell_width(cell)
    sheet.rectangle(x, segment_top, width, segment_height)

    if cell.is_label and cell.vertical_text:
        label = _cell_text(cell)
        if label:
            sheet.vertical_text(label, x, segment_top, width, segment_height)
        return

    offset = CELL_PAD_Y
    for line in lines[first_line : first_line + line_count]:
        offset += line.height
        _draw_line(sheet, line, x, width, segment_top - offset + 3)


def _draw_title(sheet: _Sheet, text: str, size: float, face: str) -> None:
    """表外通栏标题：黑体观感用粗体居中，表格从它下方开始。"""
    width = pdfmetrics.stringWidth(text, face, size)
    sheet.text(text, LEFT + (MAX_W - width) / 2, sheet.y - size, face, size)
    sheet.y -= size + 14


def _wrap(text: str, face: str, size: float, max_width: float) -> list[str]:
    """按字体实际宽度折行：中英混排与显式换行都在这里一次处理掉。"""
    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = ""
        for char in raw_line:
            if line and pdfmetrics.stringWidth(line + char, face, size) > max_width:
                lines.append(line)
                line = char
            else:
                line += char
        lines.append(line)
    return lines


def _draw_lesson_plan(sheet: _Sheet, title: str, tables: list[tuple[list[TableRowSpec], bool]]) -> None:
    """把若干张表（分隔符切出来的每一段一张）画到纸上，该翻页时翻页。"""
    # 待画行拉平成一条序列，只给「非首表的首行」标上它前面那条边界的断开方式
    # （True 真换页 / False 同页留空 / None 不用断）
    rows_to_draw = [
        (row, page_break if table_index and not row_index else None)
        for table_index, (specs, page_break) in enumerate(tables)
        for row_index, row in enumerate(specs)
    ]

    _draw_title(sheet, title, TITLE_SIZE, sheet.fonts.bold)

    span: _ActiveSpan | None = None
    for row, break_before in rows_to_draw:
        if break_before is not None:
            # 上一段的贯穿格先按已画高度收尾，不允许跨到下一张表
            if span:
                span.finish(sheet, sheet.y)
                span = None
            if break_before:
                sheet.turn_page()
            else:
                sheet.y -= TABLE_GAP
                if not sheet.room_lines(BODY_LINE_H):
                    sheet.turn_page()

        span_cell = next((cell for cell in row.cells if cell.row_span and cell.row_span > 1), None)
        own_cells = [cell for cell in row.cells if cell is not span_cell]
        # 本行行首不再被上方贯穿格占用，说明那个大格到这里已经画完
        if span and not row.covered_span:
            span.finish(sheet, sheet.y)
            span = None

        lines_of_cells = [_lines_of(cell) for cell in own_cells]
        max_lines = max((len(lines) for lines in lines_of_cells), default=0)
        if span_cell is not None:
            span = _ActiveSpan(
                cell=span_cell,
                x=TABLE_X + (row.covered_span / GRID_COLUMNS) * TABLE_W,
                top=sheet.y,
                rows_left=span_cell.row_span or 1,
            )

        # 一行放不下整格时先画放得下的部分，翻页后接着画剩余行；
        # 整行挪到下一页的话，几千字的「教学过程」会撑爆一页把内容丢在纸外。
        drawn = 0
        while drawn < max_lines:
            space = sheet.room_lines(min(LABEL_LINE_H, BODY_LINE_H))
            count = max(1, min(max_lines - drawn, space))
            height = count * min(LABEL_LINE_H, BODY_LINE_H) + CELL_PAD_Y * 2
            x = TABLE_X + (row.covered_span / GRID_COLUMNS) * TABLE_W
            for cell, lines in zip(own_cells, lines_of_cells, strict=True):
                _draw_cell_fragment(sheet, cell, lines, x, sheet.y, height, drawn, count)
                x += _cell_width(cell)
            sheet.y -= height
            drawn += count

            if drawn < max_lines:
                # 行画到一半翻页：贯穿格按已画高度收尾，不做跨页续画（竖排标题很短，几乎不会走到这）
                if span:
                    span.finish(sheet, sheet.y)
                    span = None
                sheet.turn_page()
                sheet.horizontal_line(TABLE_X, TABLE_W, TOP)

        if span:
            span.rows_left -= 1
            if span.rows_left <= 0:
                span.finish(sheet, sheet.y)
                span = None

    if span:
        span.finish(sheet, sheet.y)


def _draw_plain_body(sheet: _Sheet, title: str, content: str) -> None:
    """助手只给一段正文、没有栏目结构时：整页通栏文字，标题左对齐。"""
    _draw_title(sheet, title or "教案", 16, sheet.fonts.bold)
    face = sheet.fonts.regular
    for line in _wrap(field_value_to_text(content), face, 12, MAX_W):
        if not sheet.room_lines(BODY_LINE_H):
            sheet.turn_page()
        sheet.text(line, LEFT, sheet.y - 12, face, 12)
        sheet.y -= BODY_LINE_H


def _tables_of(
    title: str, fields: list[LayoutField], values: dict[str, Any], labels: dict[str, str]
) -> list[tuple[list[TableRowSpec], bool]]:
    """分隔符切出的每一段是一张独立的表；首行是通栏标题行，标题在表外单独画，这里剥掉。"""
    return [
        (build_table_cell_specs(title, segment.rows)[1:], segment.page_break)
        for segment in build_segments(fields, values, labels)
    ]


def generate_pdf(
    *,
    title: str,
    fields: dict[str, Any],
    field_labels: dict[str, str],
    content: str,
    layout_fields: list[LayoutField],
) -> bytes:
    """有栏目内容时按模板表格排版；否则按正文通栏输出。"""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(PAGE_W, PAGE_H))
    canvas.setTitle(title or "教案")
    canvas.setAuthor("智慧教案")

    fonts = _Fonts(
        regular=_register("regular", _REGULAR_CANDIDATES, "Helvetica"),
        bold=_register("bold", _BOLD_CANDIDATES, "Helvetica-Bold"),
    )
    sheet = _Sheet(canvas, fonts)

    spec_sets = _tables_of(title, layout_fields, fields, field_labels)
    tables = [(specs, page_break) for specs, page_break in spec_sets if specs]
    if tables:
        _draw_lesson_plan(sheet, title or "教案", tables)
    elif content:
        _draw_plain_body(sheet, title, content)

    canvas.showPage()
    canvas.save()
    return buffer.getvalue()
