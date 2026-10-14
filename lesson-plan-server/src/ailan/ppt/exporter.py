"""幻灯片文档 → pptx 字节。

导出器是"编辑器存什么就画什么"的那一端，所以入参直接吃库里的 JSONB：字段可能缺、主题可能
只填了一个色值。所有宽容都在归一化这一步做完，往下的绘制路径只碰干净数据，不逐层判空。

版式全部用原生形状与原生图表画，不预先转成图片：教师拿到文件后要能改字、改图表数据，
一张截图就改不了了。
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Iterable
from typing import Any, cast, get_args

from lxml.etree import SubElement
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.presentation import Presentation as PptxPresentation
from pptx.util import Inches, Pt

from ailan.ppt.schemas import (
    Activity,
    BigNumber,
    Chart,
    ChartSeries,
    Compare,
    CompareSide,
    Slide,
    Stage,
    Theme,
    ThemeStyle,
    TimelineStep,
)

PPTX_MIMETYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# 16:9 画布尺寸与内容区左右边距，单位英寸
SLIDE_W = 10.0
SLIDE_H = 5.625
MARGIN_X = 0.6
CONTENT_W = SLIDE_W - MARGIN_X * 2

# 主题缺省值：AI 生成或旧数据没填 theme 时兜底，导出不能因为缺字段而失败
DEFAULT_PRIMARY = "2563EB"
DEFAULT_BG = "FFFFFF"
DEFAULT_TITLE_COLOR = "1E3A8A"
DEFAULT_TEXT_COLOR = "334155"
DEFAULT_FONT = "微软雅黑"
DEFAULT_STYLE = "band"

LAYOUTS = frozenset(
    {
        "cover",
        "toc",
        "title-bullets",
        "two-column",
        "section",
        "end",
        "big-number",
        "chart",
        "timeline",
        "compare",
        "activity",
    }
)
# 允许的风格直接从字面量类型倒出来，新增一种只要改 `ThemeStyle`
_STYLES: frozenset[ThemeStyle] = frozenset(get_args(ThemeStyle))
_STAGES = frozenset({"primary", "middle", "college"})
_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")
_LINE_BREAKS = re.compile(r"\r\n?")

# 列表项符号：python-pptx 不暴露 bullet 属性，直接在文本上加前缀，
# 视觉上与 PowerPoint 的项目符号一致，不值得为它手写一段 XML
BULLET = "• "

_ALIGNMENTS = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}
_ANCHORS = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}
_CHART_TYPES = {"bar": XL_CHART_TYPE.COLUMN_CLUSTERED, "line": XL_CHART_TYPE.LINE, "pie": XL_CHART_TYPE.PIE}


class StageDesign:
    """学段设计参数：小学更圆润更花哨，大学克制、字号更小。"""

    def __init__(self, radius: float, chip_radius: float, deco: float, body_size: float) -> None:
        self.radius = radius
        self.chip_radius = chip_radius
        self.deco = deco
        self.body_size = body_size


def stage_design(stage: Stage | None) -> StageDesign:
    if stage == "primary":
        return StageDesign(0.16, 0.24, 1.0, 16)
    if stage == "college":
        return StageDesign(0.06, 0.08, 0.4, 14)
    return StageDesign(0.1, 0.14, 0.7, 15)


# ── 颜色与文本测量 ──


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.removeprefix("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgb_to_hex(rgb: Iterable[float]) -> str:
    return "".join(f"{max(0, min(255, round(value))):02X}" for value in rgb)


def _mix(a: str, b: str, t: float) -> str:
    """两色线性混合：t=0 取 a，t=1 取 b。"""
    first, second = _hex_to_rgb(a), _hex_to_rgb(b)
    return _rgb_to_hex(f + (s - f) * t for f, s in zip(first, second, strict=True))


def _tint(primary: str, t: float = 0.9) -> str:
    """主色向白色混合，得到卡片底、分隔线这类浅色。"""
    return _mix(primary, "FFFFFF", t)


def _text_width(text: str, font_size: float) -> float:
    """估算一段文本的视觉宽度（英寸）：中文与全角按一字宽，英文数字按 0.55。"""
    units = sum(1.0 if ord(char) > 0x2E80 else 0.55 for char in text)
    return units * font_size / 72


def _estimate_lines(text: str, font_size: float, width: float) -> int:
    return max(1, math.ceil(_text_width(text, font_size) / max(width, 0.1)))


def _fit_title_size(title: str, width: float) -> int:
    """标题字号超宽自动降档，避免换行溢出。"""
    for size in (26, 24, 22, 20, 18):
        if _text_width(title, size) <= width:
            return size
    return 18


# ── 归一化：库里的 JSONB 到能画的数据 ──


def _text(value: Any) -> str:
    return "" if value is None else _LINE_BREAKS.sub("\n", str(value)).strip()


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (_text(item) for item in value) if text]


def _big_number(value: Any) -> BigNumber | None:
    if not isinstance(value, dict) or not _text(value.get("value")):
        return None
    return BigNumber(
        value=_text(value.get("value")),
        unit=_text(value.get("unit")) or None,
        label=_text(value.get("label")) or None,
    )


def _chart(value: Any) -> Chart | None:
    if not isinstance(value, dict) or value.get("type") not in _CHART_TYPES:
        return None
    categories = _strings(value.get("categories"))
    series: list[ChartSeries] = []
    for item in value.get("series") or []:
        if not isinstance(item, dict):
            continue
        data = [
            float(number) if isinstance(number, (int, float)) else 0.0 for number in item.get("data") or []
        ]
        if data:
            series.append(ChartSeries(name=_text(item.get("name")) or "数据", data=data))
    # 没类别或没数值就画不出图，整块按"这页没有图表"处理
    if not categories or not series:
        return None
    return Chart(type=value["type"], categories=categories, series=series)


def _timeline(value: Any) -> list[TimelineStep]:
    if not isinstance(value, list):
        return []
    steps = []
    for item in value:
        title = _text(item.get("title")) if isinstance(item, dict) else ""
        if title:
            steps.append(TimelineStep(title=title, desc=_text(item.get("desc")) or None))
    return steps


def _compare_side(value: Any) -> CompareSide | None:
    if not isinstance(value, dict) or not _text(value.get("title")):
        return None
    return CompareSide(title=_text(value.get("title")), bullets=_strings(value.get("bullets")))


def _compare(value: Any) -> Compare | None:
    if not isinstance(value, dict):
        return None
    left, right = _compare_side(value.get("left")), _compare_side(value.get("right"))
    if left is None or right is None:
        return None
    return Compare(left=left, right=right)


def _activity(value: Any) -> Activity | None:
    if not isinstance(value, dict) or not _text(value.get("name")):
        return None
    steps = _strings(value.get("steps"))
    if not steps:
        return None
    return Activity(name=_text(value.get("name")), steps=steps)


def normalize_slide(raw: Any) -> Slide:
    """缺字段补空、空要点丢弃、版式不认识退回 title-bullets，富元素逐项校验。

    任何一项不合格都只是"这一页少画一块内容"，不让整份文件导出失败。
    """
    item = raw if isinstance(raw, dict) else {}
    layout = item.get("layout")
    timeline = _timeline(item.get("timeline"))
    return Slide(
        layout=layout if layout in LAYOUTS else "title-bullets",
        title=_text(item.get("title")),
        bullets=_strings(item.get("bullets")),
        content=_text(item.get("content")) or None,
        notes=_text(item.get("notes")) or None,
        big_number=_big_number(item.get("big_number")),
        chart=_chart(item.get("chart")),
        timeline=timeline or None,
        compare=_compare(item.get("compare")),
        activity=_activity(item.get("activity")),
    )


def _theme_of(raw: Any) -> Theme:
    """逐色校验：非法色值会让写出的文件打不开，一律退回默认而不是原样塞进 XML。"""
    theme = raw if isinstance(raw, dict) else {}

    def color(key: str, fallback: str) -> str:
        value = _text(theme.get(key))
        return value.upper() if _HEX.match(value) else fallback

    primary = color("primary", DEFAULT_PRIMARY)
    declared_style = _text(theme.get("style"))
    return Theme(
        primary=primary,
        bg=color("bg", DEFAULT_BG),
        title_color=color("title_color", DEFAULT_TITLE_COLOR),
        text_color=color("text_color", DEFAULT_TEXT_COLOR),
        font=_text(theme.get("font")) or DEFAULT_FONT,
        # 辅助色缺省时由主色提亮派生，保证旧数据也有装饰色
        accent=color("accent", _mix(primary, "FFFFFF", 0.55)),
        # 白名单外的写法一律退回默认风格；筛完剩下的就是那三个字面量之一
        style=cast("ThemeStyle", declared_style if declared_style in _STYLES else DEFAULT_STYLE),
    )


# ── 绘制底座：形状、文本、透明度 ──


def _apply_fill(shape: Any, color: str, transparency: int | None = None) -> None:
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(color)
    if transparency is not None:
        _transparency(shape._element.spPr, transparency)


def _no_line(shape: Any) -> None:
    shape.line.fill.background()


def _apply_line(shape: Any, color: str, width_pt: float) -> None:
    shape.line.color.rgb = RGBColor.from_string(color)
    shape.line.width = Pt(width_pt)


def _transparency(owner: Any, transparency: int) -> None:
    """给 solidFill 的颜色挂一个 `<a:alpha>`。

    python-pptx 的 FillFormat 没有透明度入口，只能自己补这一枚属性；
    `val` 是不透明度千分比，所以传进来的是"透明百分之几"。
    """
    if transparency <= 0:
        return
    solid = owner.find(qn("a:solidFill"))
    color = solid.find(qn("a:srgbClr")) if solid is not None else None
    if color is None:
        return
    color.append(color.makeelement(qn("a:alpha"), {"val": str((100 - transparency) * 1000)}))


def _soft_shadow(shape: Any) -> None:
    """卡片投影同样没有 API，往 spPr 末尾补一段 outerShdw；顺序正好排在 fill、ln 之后。"""
    sp_pr = shape._element.spPr
    effects = sp_pr.find(qn("a:effectLst"))
    if effects is None:
        effects = SubElement(sp_pr, qn("a:effectLst"))
    shadow = SubElement(
        effects,
        qn("a:outerShdw"),
        {"blurRad": "50800", "dist": "25400", "dir": "5400000", "rotWithShape": "0"},
    )
    color = SubElement(shadow, qn("a:srgbClr"), {"val": "000000"})
    SubElement(color, qn("a:alpha"), {"val": "12000"})


def _rect(
    page: Any,
    shape: MSO_SHAPE,
    x: float,
    y: float,
    w: float,
    h: float,
    color: str,
    radius: float | None = None,
) -> Any:
    node = page.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    _apply_fill(node, color)
    _no_line(node)
    if radius is not None:
        node.adjustments[0] = min(0.5, radius / max(min(w, h), 0.01))
    return node


def _line(page: Any, x: float, y: float, w: float, h: float, color: str, width_pt: float = 0.75) -> Any:
    """直线用连接件画：形状化的"线"在 PowerPoint 里会带填充框，导出后位置对不上。"""
    connector = page.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(x), Inches(y), Inches(x + w), Inches(y + h)
    )
    connector.line.color.rgb = RGBColor.from_string(color)
    connector.line.width = Pt(width_pt)
    return connector


def _set_typeface(run: Any, name: str) -> None:
    """`font.name` 只写 `<a:latin>`，汉字会回落到主题字体，必须补 `<a:ea>` 才真的用微软雅黑。"""
    run.font.name = name
    r_pr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        node = r_pr.find(qn(tag))
        if node is None:
            node = SubElement(r_pr, qn(tag))
        node.set("typeface", name)


def _text_box(
    page: Any,
    content: str,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    size: float,
    color: str,
    font: str,
    bold: bool = False,
    align: str | None = None,
    valign: str | None = None,
    spacing: float | None = None,
    transparency: int | None = None,
) -> None:
    """文本一律用独立文本框叠在形状上：形状自带文本会被内边距和自动缩放挪位置。

    `\n` 拆成多个段落，段间距与字号沿用调用方给的排版参数。
    """
    box = page.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    for margin in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(frame, margin, 0)
    if valign:
        frame.vertical_anchor = _ANCHORS[valign]

    for index, line in enumerate(content.split("\n")):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        if align:
            paragraph.alignment = _ALIGNMENTS[align]
        if spacing:
            paragraph.line_spacing = spacing
        run = paragraph.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor.from_string(color)
        _transparency(run._r.get_or_add_rPr(), transparency or 0)
        _set_typeface(run, font)


class Renderer:
    """一份文档一次渲染：主题、学段参数与文档标题在这里收拢，绘制方法不再层层传参。"""

    def __init__(self, theme: Theme, title: str, design: StageDesign) -> None:
        self.theme = theme
        self.title = title
        self.design = design
        self.dark = theme.style != "plain"

    @property
    def accent(self) -> str:
        """辅助色：老主题里这一列可能为空，缺省口径与归一化那步一致——主色向白提亮。"""
        return self.theme.accent or _tint(self.theme.primary, 0.55)

    # ── 通用装饰 ──

    def deco_circles(self, page: Any, on_dark: bool) -> None:
        """右上与左下各一枚半透明辅助色圆，给纯色页加点层次。"""
        accent = self.accent
        first = _rect(page, MSO_SHAPE.OVAL, SLIDE_W - 1.7, -1.1, 2.6, 2.6, accent)
        _transparency(first._element.spPr, 55 if on_dark else 70)
        second = _rect(page, MSO_SHAPE.OVAL, -0.9, SLIDE_H - 1.2, 2.0, 2.0, accent)
        _transparency(second._element.spPr, 70 if on_dark else 82)

    def footer(self, page: Any, doc_title: str, page_no: int) -> None:
        """内容页页脚：细分隔线 + 左侧文档标题 + 右侧页码。"""
        _line(page, MARGIN_X, SLIDE_H - 0.42, CONTENT_W, 0, _tint(self.theme.primary, 0.75))
        _text_box(
            page,
            doc_title,
            MARGIN_X,
            SLIDE_H - 0.38,
            CONTENT_W - 1,
            0.26,
            size=9,
            color=_tint(self.theme.primary, 0.45),
            font=self.theme.font,
        )
        _text_box(
            page,
            str(page_no),
            SLIDE_W - MARGIN_X - 0.6,
            SLIDE_H - 0.38,
            0.6,
            0.26,
            size=10,
            color=self.theme.primary,
            font=self.theme.font,
            bold=True,
            align="right",
        )

    def number_chip(self, page: Any, x: float, y: float, number: int, size: float = 0.34) -> None:
        """编号小圆 + 白字。"""
        _rect(page, MSO_SHAPE.OVAL, x, y, size, size, self.theme.primary)
        _text_box(
            page,
            str(number),
            x,
            y - 0.01,
            size,
            size,
            size=13 if size > 0.4 else 11,
            color="FFFFFF",
            font=self.theme.font,
            bold=True,
            align="center",
            valign="middle",
        )

    def bullet_cards(
        self, page: Any, bullets: list[str], x: float, y: float, w: float, available_h: float, start: int = 1
    ) -> None:
        """要点卡片列表：每条要点一张圆角卡片，自动缩字号直到整体放得进内容区。

        band 与 card 两种风格只差底色和描边，共用这一套排版。
        """
        if not bullets:
            return
        text_x = x + 0.52
        text_w = w - 0.52 - 0.2
        gap = 0.14

        for size in (15, 14, 13, 12, 11, 10):
            line_h = size * 1.45 / 72
            heights = [max(0.52, _estimate_lines(b, size, text_w) * line_h + 0.24) for b in bullets]
            if sum(heights) + gap * (len(bullets) - 1) > available_h:
                continue
            cursor = y
            for index, (bullet, height) in enumerate(zip(bullets, heights, strict=True)):
                if self.theme.style == "card":
                    node = _rect(page, MSO_SHAPE.ROUNDED_RECTANGLE, x, cursor, w, height, "FFFFFF", 0.09)
                    _apply_line(node, _tint(self.theme.primary, 0.6), 1)
                    _soft_shadow(node)
                else:
                    _rect(
                        page,
                        MSO_SHAPE.ROUNDED_RECTANGLE,
                        x,
                        cursor,
                        w,
                        height,
                        _tint(self.theme.primary, 0.9),
                        0.09,
                    )
                self.number_chip(page, x + 0.12, cursor + (height - 0.34) / 2, start + index)
                _text_box(
                    page,
                    bullet,
                    text_x,
                    cursor,
                    text_w,
                    height,
                    size=size,
                    color=self.theme.text_color,
                    font=self.theme.font,
                    valign="middle",
                    spacing=1.25,
                )
                cursor += height + gap
            return

        # 内容多到最小字号也放不下：按 10 号排一遍，允许略微压紧
        cursor = y
        line_h = 10 * 1.4 / 72
        for index, bullet in enumerate(bullets):
            height = max(0.48, _estimate_lines(bullet, 10, text_w) * line_h + 0.2)
            _rect(
                page, MSO_SHAPE.ROUNDED_RECTANGLE, x, cursor, w, height, _tint(self.theme.primary, 0.9), 0.08
            )
            self.number_chip(page, x + 0.12, cursor + (height - 0.32) / 2, start + index, 0.32)
            _text_box(
                page,
                bullet,
                text_x,
                cursor,
                text_w,
                height,
                size=10,
                color=self.theme.text_color,
                font=self.theme.font,
                valign="middle",
            )
            cursor += height + 0.1

    def content_header(self, page: Any, spec: Slide, page_no: int) -> None:
        """富版式通用页眉：标题 + 主色短杠 + 右上角署名 + 页脚 + 学段装饰。"""
        _text_box(
            page,
            spec.title,
            MARGIN_X,
            0.4,
            CONTENT_W,
            0.62,
            size=_fit_title_size(spec.title, CONTENT_W - 1.2),
            color=self.theme.title_color,
            font=self.theme.font,
            bold=True,
            valign="middle",
        )
        _rect(page, MSO_SHAPE.RECTANGLE, MARGIN_X, 1.12, 0.7, 0.06, self.theme.primary)
        _text_box(
            page,
            "智慧教案",
            SLIDE_W - MARGIN_X - 1.2,
            0.46,
            1.2,
            0.3,
            size=9,
            color=_tint(self.theme.primary, 0.55),
            font=self.theme.font,
            align="right",
        )
        self.footer(page, self.title, page_no)
        # 大学课件不加装饰圆，小学的最显眼
        if self.design.deco > 0.5:
            node = _rect(page, MSO_SHAPE.OVAL, SLIDE_W - 1.2, -0.8, 1.8, 1.8, self.accent)
            _transparency(node._element.spPr, round(90 - self.design.deco * 25))

    # ── 各版式 ──

    def cover(self, page: Any, spec: Slide) -> None:
        heading = spec.title or self.title
        if self.dark:
            _rect(page, MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, SLIDE_H, self.theme.primary)
            self.deco_circles(page, on_dark=True)
            chip = _rect(page, MSO_SHAPE.ROUNDED_RECTANGLE, MARGIN_X, 0.5, 1.5, 0.4, self.accent, 0.2)
            _transparency(chip._element.spPr, 20)
            _text_box(
                page,
                "智慧课件",
                MARGIN_X,
                0.5,
                1.5,
                0.4,
                size=12,
                color="FFFFFF",
                font=self.theme.font,
                bold=True,
                align="center",
                valign="middle",
            )
            _text_box(
                page,
                heading,
                MARGIN_X + 0.3,
                2.0,
                CONTENT_W - 0.6,
                1.1,
                size=30 if _text_width(heading, 38) > CONTENT_W - 0.6 else 38,
                color="FFFFFF",
                font=self.theme.font,
                bold=True,
                align="center",
                valign="middle",
            )
            bar = _rect(page, MSO_SHAPE.RECTANGLE, SLIDE_W / 2 - 0.6, 3.2, 1.2, 0.06, "FFFFFF")
            _transparency(bar._element.spPr, 30)
        else:
            _rect(page, MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, 0.14, self.theme.primary)
            self.deco_circles(page, on_dark=False)
            _text_box(
                page,
                heading,
                MARGIN_X + 0.3,
                2.0,
                CONTENT_W - 0.6,
                1.1,
                size=36,
                color=self.theme.title_color,
                font=self.theme.font,
                bold=True,
                align="center",
                valign="middle",
            )
            _rect(page, MSO_SHAPE.RECTANGLE, SLIDE_W / 2 - 0.6, 3.2, 1.2, 0.06, self.theme.primary)

        subtitle = spec.bullets[0] if spec.bullets else spec.content
        if subtitle:
            if self.dark:
                _text_box(
                    page,
                    subtitle,
                    MARGIN_X + 0.6,
                    3.45,
                    CONTENT_W - 1.2,
                    0.5,
                    size=16,
                    color="FFFFFF",
                    font=self.theme.font,
                    align="center",
                    valign="middle",
                    transparency=15,
                )
            else:
                _text_box(
                    page,
                    subtitle,
                    MARGIN_X + 0.6,
                    3.45,
                    CONTENT_W - 1.2,
                    0.5,
                    size=16,
                    color=self.theme.text_color,
                    font=self.theme.font,
                    align="center",
                    valign="middle",
                )
        # 副标题之外的要点了并成一行信息（科目 · 年级这类）
        info = "　·　".join(bullet for bullet in spec.bullets[1:] if bullet)
        if info and self.dark:
            _text_box(
                page,
                info,
                MARGIN_X,
                4.75,
                CONTENT_W,
                0.4,
                size=13,
                color="FFFFFF",
                font=self.theme.font,
                align="center",
                valign="middle",
                transparency=25,
            )

    def toc(self, page: Any, spec: Slide, entries: list[str]) -> None:
        """目录：编号卡片按条目数决定单双栏。"""
        _text_box(
            page,
            spec.title or "目录",
            MARGIN_X,
            0.42,
            CONTENT_W,
            0.6,
            size=26,
            color=self.theme.title_color,
            font=self.theme.font,
            bold=True,
        )
        _rect(page, MSO_SHAPE.RECTANGLE, MARGIN_X, 1.12, 0.9, 0.07, self.theme.primary)
        self.deco_circles(page, on_dark=False)
        if not entries:
            return

        columns = 2 if len(entries) > 6 else 1
        rows = math.ceil(len(entries) / columns)
        column_w = (CONTENT_W - 0.4) / 2 if columns == 2 else CONTENT_W
        card_h = min(0.62, (SLIDE_H - 1.55 - 0.5) / max(rows, 1) - 0.12)
        for index, entry in enumerate(entries):
            column, row = divmod(index, rows)
            x = MARGIN_X + column * (column_w + 0.4)
            y = 1.55 + row * (card_h + 0.12)
            _rect(
                page,
                MSO_SHAPE.ROUNDED_RECTANGLE,
                x,
                y,
                column_w,
                card_h,
                _tint(self.theme.primary, 0.92),
                0.1,
            )
            self.number_chip(page, x + 0.14, y + (card_h - 0.34) / 2, index + 1)
            _text_box(
                page,
                entry,
                x + 0.62,
                y,
                column_w - 0.62 - 0.15,
                card_h,
                size=14,
                color=self.theme.text_color,
                font=self.theme.font,
                valign="middle",
            )

    def section(self, page: Any, spec: Slide, index: int) -> None:
        """章节页：满版底色 + 右上角大编号水印。"""
        number = f"{index:02d}"
        if self.dark:
            _rect(page, MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, SLIDE_H, self.theme.primary)
            self.deco_circles(page, on_dark=True)
            _text_box(
                page,
                number,
                SLIDE_W - 2.6,
                0.6,
                2.2,
                1.6,
                size=72,
                color="FFFFFF",
                font=self.theme.font,
                bold=True,
                align="right",
                valign="middle",
                transparency=75,
            )
            bar = _rect(page, MSO_SHAPE.RECTANGLE, MARGIN_X, 2.62, 1.0, 0.07, "FFFFFF")
            _transparency(bar._element.spPr, 25)
            _text_box(
                page,
                spec.title,
                MARGIN_X,
                2.85,
                CONTENT_W,
                0.9,
                size=30,
                color="FFFFFF",
                font=self.theme.font,
                bold=True,
                valign="middle",
            )
            sub = spec.bullets[0] if spec.bullets else spec.content
            if sub:
                _text_box(
                    page,
                    sub,
                    MARGIN_X,
                    3.8,
                    CONTENT_W - 1,
                    0.5,
                    size=14,
                    color="FFFFFF",
                    font=self.theme.font,
                    transparency=20,
                )
        else:
            _rect(page, MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, SLIDE_H, _tint(self.theme.primary, 0.93))
            _text_box(
                page,
                number,
                SLIDE_W - 2.6,
                0.6,
                2.2,
                1.6,
                size=72,
                color=self.accent,
                font=self.theme.font,
                bold=True,
                align="right",
                valign="middle",
            )
            _rect(page, MSO_SHAPE.RECTANGLE, MARGIN_X, 2.62, 1.0, 0.07, self.theme.primary)
            _text_box(
                page,
                spec.title,
                MARGIN_X,
                2.85,
                CONTENT_W,
                0.9,
                size=30,
                color=self.theme.title_color,
                font=self.theme.font,
                bold=True,
                valign="middle",
            )

    def end(self, page: Any, spec: Slide) -> None:
        heading = spec.title or "谢谢观看"
        if self.dark:
            _rect(page, MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, SLIDE_H, self.theme.primary)
            self.deco_circles(page, on_dark=True)
            _text_box(
                page,
                heading,
                MARGIN_X,
                2.35,
                CONTENT_W,
                1.0,
                size=34,
                color="FFFFFF",
                font=self.theme.font,
                bold=True,
                align="center",
                valign="middle",
            )
            bar = _rect(page, MSO_SHAPE.RECTANGLE, SLIDE_W / 2 - 0.6, 3.5, 1.2, 0.06, "FFFFFF")
            _transparency(bar._element.spPr, 30)
            sub = spec.bullets[0] if spec.bullets else spec.content
            if sub:
                _text_box(
                    page,
                    sub,
                    MARGIN_X + 0.6,
                    3.72,
                    CONTENT_W - 1.2,
                    0.5,
                    size=14,
                    color="FFFFFF",
                    font=self.theme.font,
                    align="center",
                    valign="middle",
                    transparency=20,
                )
        else:
            _rect(page, MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, 0.14, self.theme.primary)
            self.deco_circles(page, on_dark=False)
            _text_box(
                page,
                heading,
                MARGIN_X,
                2.35,
                CONTENT_W,
                1.0,
                size=34,
                color=self.theme.title_color,
                font=self.theme.font,
                bold=True,
                align="center",
                valign="middle",
            )

    def bullets(self, page: Any, spec: Slide, page_no: int) -> None:
        """标题 + 要点卡片；two-column 时前后分栏、编号连续。"""
        _text_box(
            page,
            spec.title,
            MARGIN_X,
            0.4,
            CONTENT_W,
            0.62,
            size=_fit_title_size(spec.title, CONTENT_W - 1.2),
            color=self.theme.title_color,
            font=self.theme.font,
            bold=True,
            valign="middle",
        )
        _rect(page, MSO_SHAPE.RECTANGLE, MARGIN_X, 1.12, 0.7, 0.06, self.theme.primary)
        _text_box(
            page,
            "智慧教案",
            SLIDE_W - MARGIN_X - 1.2,
            0.46,
            1.2,
            0.3,
            size=9,
            color=_tint(self.theme.primary, 0.55),
            font=self.theme.font,
            align="right",
        )
        self.footer(page, self.title, page_no)

        body_y = 1.42
        body_h = SLIDE_H - body_y - 0.55
        if spec.layout == "two-column" and len(spec.bullets) >= 2:
            half = math.ceil(len(spec.bullets) / 2)
            column_w = (CONTENT_W - 0.4) / 2
            self.bullet_cards(page, spec.bullets[:half], MARGIN_X, body_y, column_w, body_h)
            self.bullet_cards(
                page, spec.bullets[half:], MARGIN_X + column_w + 0.4, body_y, column_w, body_h, half + 1
            )
        elif spec.bullets:
            self.bullet_cards(page, spec.bullets, MARGIN_X, body_y, CONTENT_W, body_h)
        elif spec.content:
            _rect(
                page,
                MSO_SHAPE.ROUNDED_RECTANGLE,
                MARGIN_X,
                body_y,
                CONTENT_W,
                body_h,
                _tint(self.theme.primary, 0.94),
                0.12,
            )
            _text_box(
                page,
                spec.content,
                MARGIN_X + 0.3,
                body_y + 0.2,
                CONTENT_W - 0.6,
                body_h - 0.4,
                size=15,
                color=self.theme.text_color,
                font=self.theme.font,
                spacing=1.5,
                valign="top",
            )
        else:
            _text_box(
                page,
                "（本页暂无内容）",
                MARGIN_X,
                body_y,
                CONTENT_W,
                0.5,
                size=14,
                color=_tint(self.theme.primary, 0.45),
                font=self.theme.font,
            )

    def big_number(self, page: Any, spec: Slide, page_no: int) -> None:
        self.content_header(page, spec, page_no)
        number = spec.big_number
        assert number is not None  # 由 paint 的分发条件保证
        body_y = 1.5
        body_h = SLIDE_H - body_y - 0.55
        left_w = 4.0

        _rect(
            page,
            MSO_SHAPE.ROUNDED_RECTANGLE,
            MARGIN_X,
            body_y,
            left_w,
            body_h,
            _tint(self.theme.primary, 0.92),
            self.design.radius,
        )
        # 数字越长字号越小，单位始终比数字小一档
        digits = len(number.value)
        size = 96 if digits <= 3 else 70 if digits <= 5 else 52
        _text_box(
            page,
            f"{number.value} {number.unit}" if number.unit else number.value,
            MARGIN_X + 0.2,
            body_y + 0.2,
            left_w - 0.4,
            body_h - 0.4,
            size=size,
            color=self.theme.primary,
            font=self.theme.font,
            bold=True,
            align="center",
            valign="middle",
        )

        right_x = MARGIN_X + left_w + 0.4
        right_w = CONTENT_W - left_w - 0.4
        cursor = body_y + 0.1
        if number.label:
            _text_box(
                page,
                number.label,
                right_x,
                cursor,
                right_w,
                0.6,
                size=19,
                color=self.theme.title_color,
                font=self.theme.font,
                bold=True,
                valign="middle",
            )
            cursor += 0.7
        for bullet in spec.bullets:
            line_h = self.design.body_size * 1.4 / 72
            height = max(0.5, _estimate_lines(bullet, self.design.body_size, right_w - 0.36) * line_h + 0.2)
            node = _rect(
                page,
                MSO_SHAPE.ROUNDED_RECTANGLE,
                right_x,
                cursor,
                right_w,
                height,
                "FFFFFF",
                self.design.radius * 0.7,
            )
            _apply_line(node, _tint(self.theme.primary, 0.6), 1)
            _text_box(
                page,
                bullet,
                right_x + 0.18,
                cursor,
                right_w - 0.36,
                height,
                size=self.design.body_size,
                color=self.theme.text_color,
                font=self.theme.font,
                valign="middle",
                spacing=1.2,
            )
            cursor += height + 0.12

    def chart(self, page: Any, spec: Slide, page_no: int) -> None:
        self.content_header(page, spec, page_no)
        data = spec.chart
        assert data is not None
        body_y = 1.5
        chart_w = 5.7
        chart_h = SLIDE_H - body_y - 0.6

        chart_data = CategoryChartData()
        chart_data.categories = data.categories
        for series in data.series:
            chart_data.add_series(series.name, series.data)
        frame = page.shapes.add_chart(
            _CHART_TYPES[data.type],
            Inches(MARGIN_X),
            Inches(body_y),
            Inches(chart_w),
            Inches(chart_h),
            chart_data,
        )
        native = frame.chart
        native.has_legend = len(data.series) > 1 or data.type == "pie"
        if native.has_legend:
            native.legend.position = XL_LEGEND_POSITION.BOTTOM
            native.legend.include_in_layout = False
            native.legend.font.size = Pt(11)
            native.legend.font.color.rgb = RGBColor.from_string(self.theme.text_color)
        # 配色围绕主色成一套：主色、辅助色、加深、减淡，多系列时循环
        palette = [
            self.theme.primary,
            self.accent,
            _mix(self.theme.primary, "000000", 0.25),
            _tint(self.theme.primary, 0.5),
        ]
        for index, series in enumerate(native.series):
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = RGBColor.from_string(palette[index % len(palette)])
        if data.type == "pie":
            plot = native.plots[0]
            plot.has_data_labels = True
            plot.data_labels.font.size = Pt(10)
            plot.data_labels.font.color.rgb = RGBColor.from_string(self.theme.text_color)
        else:
            native.category_axis.tick_labels.font.size = Pt(11)
            native.category_axis.tick_labels.font.color.rgb = RGBColor.from_string(self.theme.text_color)
            native.value_axis.tick_labels.font.size = Pt(10)
            native.value_axis.tick_labels.font.color.rgb = RGBColor.from_string(self.theme.text_color)

        right_x = MARGIN_X + chart_w + 0.35
        right_w = CONTENT_W - chart_w - 0.35
        _rect(
            page,
            MSO_SHAPE.ROUNDED_RECTANGLE,
            right_x,
            body_y,
            right_w,
            chart_h,
            _tint(self.theme.primary, 0.93),
            self.design.radius,
        )
        _text_box(
            page,
            "数据解读",
            right_x + 0.2,
            body_y + 0.16,
            right_w - 0.4,
            0.34,
            size=13,
            color=self.theme.title_color,
            font=self.theme.font,
            bold=True,
        )
        notes = spec.bullets or ["（结合图表补充结论）"]
        _text_box(
            page,
            "\n".join(BULLET + note for note in notes),
            right_x + 0.2,
            body_y + 0.6,
            right_w - 0.4,
            chart_h - 0.8,
            size=self.design.body_size,
            color=self.theme.text_color,
            font=self.theme.font,
            spacing=1.3,
            valign="top",
        )

    def timeline(self, page: Any, spec: Slide, page_no: int) -> None:
        """时间线：五步以内走横向轨道，再多就换成竖排列表。"""
        self.content_header(page, spec, page_no)
        steps = spec.timeline or []
        if not steps:
            return
        body_y = 1.55
        count = len(steps)

        if count <= 5:
            track_y = 2.85
            _line(page, MARGIN_X + 0.3, track_y, CONTENT_W - 0.6, 0, _tint(self.theme.primary, 0.5), 2)
            slot_w = (CONTENT_W - 0.6) / count
            for index, step in enumerate(steps):
                center = MARGIN_X + 0.3 + slot_w * index + slot_w / 2
                node = _rect(
                    page, MSO_SHAPE.OVAL, center - 0.18, track_y - 0.18, 0.36, 0.36, self.theme.primary
                )
                _apply_line(node, self.theme.bg, 2)
                _text_box(
                    page,
                    str(index + 1),
                    center - 0.18,
                    track_y - 0.18,
                    0.36,
                    0.36,
                    size=13,
                    color="FFFFFF",
                    font=self.theme.font,
                    bold=True,
                    align="center",
                    valign="middle",
                )
                _text_box(
                    page,
                    step.title,
                    center - slot_w / 2 + 0.05,
                    body_y + 0.05,
                    slot_w - 0.1,
                    0.6,
                    size=14,
                    color=self.theme.title_color,
                    font=self.theme.font,
                    bold=True,
                    align="center",
                    valign="bottom",
                )
                if step.desc:
                    _text_box(
                        page,
                        step.desc,
                        center - slot_w / 2 + 0.08,
                        track_y + 0.28,
                        slot_w - 0.16,
                        1.1,
                        size=11.5,
                        color=self.theme.text_color,
                        font=self.theme.font,
                        align="center",
                        valign="top",
                        spacing=1.2,
                    )
            return

        line_x = MARGIN_X + 0.25
        _line(page, line_x, body_y + 0.2, 0, 3.0, _tint(self.theme.primary, 0.5), 2)
        row_h = min(0.72, 3.3 / count)
        for index, step in enumerate(steps):
            y = body_y + 0.15 + row_h * index
            _rect(page, MSO_SHAPE.OVAL, line_x - 0.16, y, 0.32, 0.32, self.theme.primary)
            _text_box(
                page,
                str(index + 1),
                line_x - 0.16,
                y,
                0.32,
                0.32,
                size=12,
                color="FFFFFF",
                font=self.theme.font,
                bold=True,
                align="center",
                valign="middle",
            )
            _text_box(
                page,
                step.title,
                line_x + 0.35,
                y - 0.02,
                3.0,
                0.34,
                size=14,
                color=self.theme.title_color,
                font=self.theme.font,
                bold=True,
                valign="middle",
            )
            if step.desc:
                _text_box(
                    page,
                    step.desc,
                    line_x + 0.35,
                    y + 0.3,
                    CONTENT_W - 0.9,
                    max(row_h - 0.28, 0.2),
                    size=11.5,
                    color=self.theme.text_color,
                    font=self.theme.font,
                    valign="top",
                )

    def compare(self, page: Any, spec: Slide, page_no: int) -> None:
        self.content_header(page, spec, page_no)
        both = spec.compare
        assert both is not None
        body_y = 1.5
        body_h = SLIDE_H - body_y - 0.55
        gap = 0.7
        column_w = (CONTENT_W - gap) / 2
        sides = [
            (both.left, MARGIN_X, self.theme.primary),
            (both.right, MARGIN_X + column_w + gap, _mix(self.theme.primary, "000000", 0.22)),
        ]
        for side, x, color in sides:
            _rect(page, MSO_SHAPE.ROUNDED_RECTANGLE, x, body_y, column_w, 0.55, color, self.design.radius)
            _text_box(
                page,
                side.title,
                x,
                body_y,
                column_w,
                0.55,
                size=17,
                color="FFFFFF",
                font=self.theme.font,
                bold=True,
                align="center",
                valign="middle",
            )
            list_y = body_y + 0.7
            list_h = body_h - 0.7
            _rect(
                page,
                MSO_SHAPE.ROUNDED_RECTANGLE,
                x,
                list_y,
                column_w,
                list_h,
                _tint(color, 0.94),
                self.design.radius,
            )
            if side.bullets:
                _text_box(
                    page,
                    "\n".join(BULLET + bullet for bullet in side.bullets),
                    x + 0.22,
                    list_y + 0.18,
                    column_w - 0.44,
                    list_h - 0.36,
                    size=self.design.body_size,
                    color=self.theme.text_color,
                    font=self.theme.font,
                    spacing=1.35,
                    valign="top",
                )
        # 中间那枚 VS 圆压住两侧表头，最后画才不会被盖掉
        _rect(page, MSO_SHAPE.OVAL, SLIDE_W / 2 - 0.27, body_y + 0.02, 0.54, 0.54, self.theme.bg)
        _text_box(
            page,
            "VS",
            SLIDE_W / 2 - 0.27,
            body_y + 0.02,
            0.54,
            0.54,
            size=15,
            color=self.theme.primary,
            font=self.theme.font,
            bold=True,
            align="center",
            valign="middle",
        )

    def activity(self, page: Any, spec: Slide, page_no: int) -> None:
        self.content_header(page, spec, page_no)
        item = spec.activity
        assert item is not None
        body_y = 1.5
        _rect(
            page,
            MSO_SHAPE.ROUNDED_RECTANGLE,
            MARGIN_X,
            body_y,
            CONTENT_W,
            0.72,
            self.theme.primary,
            self.design.radius,
        )
        chip = _rect(
            page, MSO_SHAPE.ROUNDED_RECTANGLE, MARGIN_X + 0.2, body_y + 0.16, 1.25, 0.4, "FFFFFF", 0.2
        )
        _transparency(chip._element.spPr, 78)
        _text_box(
            page,
            "课堂活动",
            MARGIN_X + 0.2,
            body_y + 0.16,
            1.25,
            0.4,
            size=12,
            color="FFFFFF",
            font=self.theme.font,
            bold=True,
            align="center",
            valign="middle",
        )
        _text_box(
            page,
            item.name,
            MARGIN_X + 1.65,
            body_y,
            CONTENT_W - 1.85,
            0.72,
            size=19,
            color="FFFFFF",
            font=self.theme.font,
            bold=True,
            valign="middle",
        )

        list_y = body_y + 0.92
        list_h = SLIDE_H - list_y - 0.6
        count = len(item.steps)
        if count <= 4:
            card_gap = 0.25
            card_w = (CONTENT_W - card_gap * (count - 1)) / count
            for index, step in enumerate(item.steps):
                x = MARGIN_X + index * (card_w + card_gap)
                node = _rect(
                    page, MSO_SHAPE.ROUNDED_RECTANGLE, x, list_y, card_w, list_h, "FFFFFF", self.design.radius
                )
                _apply_line(node, _tint(self.theme.primary, 0.6), 1)
                _soft_shadow(node)
                _rect(
                    page, MSO_SHAPE.OVAL, x + card_w / 2 - 0.22, list_y + 0.2, 0.44, 0.44, self.theme.primary
                )
                _text_box(
                    page,
                    str(index + 1),
                    x + card_w / 2 - 0.22,
                    list_y + 0.2,
                    0.44,
                    0.44,
                    size=16,
                    color="FFFFFF",
                    font=self.theme.font,
                    bold=True,
                    align="center",
                    valign="middle",
                )
                _text_box(
                    page,
                    step,
                    x + 0.15,
                    list_y + 0.78,
                    card_w - 0.3,
                    list_h - 0.9,
                    size=13,
                    color=self.theme.text_color,
                    font=self.theme.font,
                    align="center",
                    valign="top",
                    spacing=1.3,
                )
        else:
            self.bullet_cards(page, item.steps, MARGIN_X, list_y, CONTENT_W, list_h)


def _toc_entries(slides: list[Slide]) -> list[str]:
    """目录条目：取内容页与章节页的标题。"""
    return [
        slide.title
        for slide in slides
        if slide.layout in ("title-bullets", "two-column", "section") and slide.title
    ]


def _paint(page: Any, renderer: Renderer, spec: Slide, index: int, slides: list[Slide]) -> None:
    if spec.layout == "cover":
        renderer.cover(page, spec)
    elif spec.layout == "toc":
        renderer.toc(page, spec, spec.bullets or _toc_entries(slides))
    elif spec.layout == "section":
        renderer.section(page, spec, index)
    elif spec.layout == "end":
        renderer.end(page, spec)
    elif spec.layout == "big-number" and spec.big_number:
        renderer.big_number(page, spec, index + 1)
    elif spec.layout == "chart" and spec.chart:
        renderer.chart(page, spec, index + 1)
    elif spec.layout == "timeline" and spec.timeline:
        renderer.timeline(page, spec, index + 1)
    elif spec.layout == "compare" and spec.compare:
        renderer.compare(page, spec, index + 1)
    elif spec.layout == "activity" and spec.activity:
        renderer.activity(page, spec, index + 1)
    else:
        renderer.bullets(page, spec, index + 1)


def _blank_layout(prs: PptxPresentation) -> Any:
    """挑一页空白版式当画纸：课件每块形状都是自己摆的，不需要母版占位符。"""
    for layout in prs.slide_layouts:
        if layout.name in ("Blank", "空白"):
            return layout
    return prs.slide_layouts[6]


def export_pptx(raw: dict[str, Any]) -> bytes:
    """文档字典 → pptx 字节。

    入参是库里的 JSONB，可能来自 AI、编辑器或旧版本，所以先整体归一化，
    绘制路径上不再碰未清洗的数据。
    """
    doc = raw if isinstance(raw, dict) else {}
    theme = _theme_of(doc.get("theme"))
    title = _text(doc.get("title")) or "幻灯片"
    renderer = Renderer(
        theme, title, stage_design(doc.get("stage") if doc.get("stage") in _STAGES else "middle")
    )
    slides = [normalize_slide(item) for item in doc.get("slides") or []]

    cover = Slide(layout="cover", title=title)
    # 一页都没有也要给出能打开的文件；首页不是封面时补一页，结构才完整
    if not slides:
        slides.append(cover)
    if slides[0].layout != "cover":
        slides.insert(0, cover)

    prs = Presentation()
    prs.core_properties.title = title
    prs.core_properties.author = "智慧教案"
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    layout = _blank_layout(prs)

    for index, spec in enumerate(slides):
        page = prs.slides.add_slide(layout)
        page.background.fill.solid()
        page.background.fill.fore_color.rgb = RGBColor.from_string(theme.bg)
        _paint(page, renderer, spec, index, slides)
        frame = page.notes_slide.notes_text_frame
        if spec.notes and frame is not None:
            frame.text = spec.notes

    stream = io.BytesIO()
    prs.save(stream)
    return stream.getvalue()
