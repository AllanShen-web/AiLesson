"""模板槽位的纯函数层：挑槽、算字数预算、清水印、切批次。

这些判断决定"模型看到什么、能写多少字、哪些页该删"，全是可脱离数据库和模型单测的纯逻辑，
所以集中放在这里；`service.py` 只负责编排与调用模型。

槽位是改写链路的最小单位：一个可写文本容器 = 一个槽，
`target` 形如 `3-15`（解析时的 0 基页序 + 形状 id），删页之前它一直有效。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ailan.ppt.schemas import SetTextOp, TemplateStructure

# 可交给改写的文本容器：普通文本形状与表格；图片/图表/组合容器只能存档
WRITABLE_KINDS = frozenset({"placeholder", "textbox", "table"})

# 页面家具占位符：日期、页脚、页码、页眉。写进去会把活字段变成死文字，
# 也不该让模型拿教案内容去填它们，所以只走水印清洗，不进改写清单。
FURNITURE_PH = frozenset({"DATE", "FOOTER", "SLIDE_NUMBER", "HEADER"})

# 一次模型调用最多改写的模板页数：再多就吃满输出预算，靠后的页开始漏槽
REWRITE_PAGES_PER_BATCH = 3

# 改写批次并发数：再高就要排队等模型限流，收益不再增长
REWRITE_CONCURRENCY = 4

# 页规划单批页数：只回页码数组，一批 15 页仍远在输出预算内
PAGE_PLAN_BATCH = 15

# 页规划批次并发数
PLAN_CONCURRENCY = 3

# 来源水印：下载站署名与网址。识别靠"短行 + 网址或推广词"，
# 长句里出现网址的多是正文内容，不动它，宁可漏清也不许误伤教学文本。
WATERMARK_URL = re.compile(
    r"(https?://|www\.)\S+|[a-z0-9][a-z0-9-]*\.(com|cn|net|org|xyz|top|vip|club|info)(\.[a-z]{2})?",
    re.IGNORECASE,
)
WATERMARK_WORDS = (
    "第一ppt",
    "1ppt",
    "ppt模板",
    "精品ppt",
    "免费ppt",
    "模板下载",
    "下载网站",
    "上传者",
    "版权所有",
    "未经许可",
    "仅供学习",
    "扫码关注",
    "公众号",
    "交流qq",
    "加群",
)

_TITLE_PH = re.compile(r"TITLE|SUBTITLE", re.IGNORECASE)
_BODY_PH = re.compile(r"BODY|OBJECT", re.IGNORECASE)
_LINE_BREAKS = re.compile(r"\r\n?")
_TRAILING_PUNCT = re.compile(r"[\s，、；：]+$")
_FUZZ_BLANK = re.compile(r"\s+")

# 句号级与顿号级句读：收口时优先落在句号级
HARD_STOPS = "。！？；"
SOFT_STOPS = "，、 "


@dataclass
class RewriteSlot:
    """模板里一个可交给改写的文本槽。

    `limit` 是该槽的字数上限，超了回写会撑破文本框，所以进提示词前就得算好。
    """

    target: str
    kind: str
    placeholder_type: str | None
    text: str
    limit: int


@dataclass
class PageDigest:
    """页摘要，供页规划模型判断取舍：页码、文本槽数与截断后的正文。"""

    slide: int
    slots: int
    text: str

    def as_prompt_row(self) -> dict[str, Any]:
        return {"slide": self.slide, "slots": self.slots, "text": self.text}


@dataclass
class WatermarkPlan:
    """水印清洗的落地产物：整槽清空、混排槽的替换文本、清洗后彻底无字的页。"""

    blank_targets: set[str] = field(default_factory=set)
    text_ops: list[SetTextOp] = field(default_factory=list)
    empty_slides: list[int] = field(default_factory=list)


class _HasTarget(Protocol):
    target: str


def page_of_target(target: str) -> int | None:
    """拆出 `target` 前段的 0 基页序；不是数字就是脏数据，交回 None 由调用方丢弃。"""
    head = str(target).split("-", 1)[0]
    try:
        return int(head)
    except ValueError:
        return None


def slot_text_budget(text: str | None, placeholder_type: str | None) -> int:
    """槽位字数预算。

    模板槽位原文常常只有几个字（样例中位数 4 字），纯按"原文 ×1.2"会把教学要点砍成三字经，
    因此按比例算出的预算再按标题/正文托一个绝对下限；空槽没有原文可参照，按占位符类型给保守值。
    """
    original = text or ""
    titleish = bool(_TITLE_PH.search(placeholder_type or ""))
    if not original:
        return 80 if _BODY_PH.search(placeholder_type or "") else 24 if titleish else 40
    ratio = math.ceil(len(original) * 1.2)
    single_line = "\n" not in original
    floor = 24 if (titleish or (single_line and len(original) <= 12)) else 80
    return max(ratio, floor)


def text_slots(structure: TemplateStructure | None) -> list[RewriteSlot]:
    """模板里所有可写文本容器，含空占位符与页面家具。

    空槽没有原文可比对，水印清洗与"最终留白"统计要靠它，所以这里不筛掉。
    """
    out: list[RewriteSlot] = []
    for slide in structure.slides if structure else []:
        for shape in slide.shapes:
            if shape.kind not in WRITABLE_KINDS:
                continue
            text = shape.text or ""
            out.append(
                RewriteSlot(
                    target=shape.target,
                    kind=shape.kind,
                    placeholder_type=shape.placeholder_type,
                    text=text,
                    limit=slot_text_budget(text, shape.placeholder_type),
                )
            )
    return out


def rewrite_slots(structure: TemplateStructure | None) -> list[RewriteSlot]:
    """交给模型改写的槽：模板上有字的、且不是页码日期页脚这类页面家具。"""
    return [
        slot
        for slot in text_slots(structure)
        if slot.text.strip() and (slot.placeholder_type or "") not in FURNITURE_PH
    ]


def template_text_map(structure: TemplateStructure, deleted: Iterable[int]) -> dict[str, str]:
    """模板原文映射，键用删页后的新页序：形状 id 不随删页变化，页码会变。

    回读自检据此判断产物里哪个框还逐字抄着模板。
    """
    drop = set(deleted)
    mapping: dict[str, str] = {}
    removed = 0
    for slide in structure.slides:
        if slide.index in drop:
            removed += 1
            continue
        for shape in slide.shapes:
            if shape.kind not in WRITABLE_KINDS or not shape.text:
                continue
            mapping[f"{slide.index - removed}-{shape.shape_id}"] = shape.text
    return mapping


def find_echoed_slots(slots: Iterable[RewriteSlot], original_text: Mapping[str, str]) -> list[RewriteSlot]:
    """产物中与模板逐字相同的框：模型照抄了，等于这页根本没改写。"""
    return [slot for slot in slots if slot.text and original_text.get(slot.target) == slot.text]


def stray_watermark_ops(slots: Iterable[RewriteSlot]) -> list[SetTextOp]:
    """产物里残留的来源水印，含模型把署名抄进其它框的情形。"""
    out: list[SetTextOp] = []
    for slot in slots:
        cleaned = strip_watermark(slot)
        if cleaned is not None and cleaned != slot.text:
            out.append(SetTextOp(target=slot.target, text=cleaned))
    return out


def count_unwritable_texts(structure: TemplateStructure) -> int:
    """有文字但改写碰不到的形状（图表、嵌入对象等），数量如实进报告。"""
    return sum(
        1
        for slide in structure.slides
        for shape in slide.shapes
        if shape.text and shape.kind not in WRITABLE_KINDS
    )


def is_watermark_line(line: str | None) -> bool:
    text = (line or "").strip()
    if not text or len(text) > 30:
        return False
    lowered = text.lower()
    return bool(WATERMARK_URL.search(text)) or any(word in lowered for word in WATERMARK_WORDS)


def strip_watermark(slot: RewriteSlot) -> str | None:
    """剔除槽位里的水印。

    文本框按段删行，表格按格清空（`\\n` 行、`\\t` 列、`\\v` 格内段，网格结构不变）。
    返回 None 表示该槽没有水印；返回空串表示整槽都是水印。
    """
    source = slot.text or ""
    if not source:
        return None
    if slot.kind == "table":
        hit = False
        rows: list[str] = []
        for row in source.split("\n"):
            cells: list[str] = []
            for cell in row.split("\t"):
                lines = cell.split("\v")
                kept = [line for line in lines if not is_watermark_line(line)]
                if len(kept) != len(lines):
                    hit = True
                cells.append("\v".join(kept))
            rows.append("\t".join(cells))
        return "\n".join(rows) if hit else None
    lines = source.split("\n")
    kept = [line for line in lines if not is_watermark_line(line)]
    return None if len(kept) == len(lines) else "\n".join(kept)


def plan_watermark_cleaning(slots: Iterable[RewriteSlot]) -> WatermarkPlan:
    """逐槽找水印。

    整槽只有水印的直接清空并退出改写清单（留个空框比让模型硬凑一句话好）；
    混排水印只给兜底清洗指令，模型若已重写该槽就以模型为准。
    某页文本槽在清洗后全部为空，说明这页只是下载站署名页，判为废页。
    判废页只认"除了水印什么都不剩"，像"目录"这种还有实际内容的短标签不算废页，宁可多留一页。
    """
    plan = WatermarkPlan()
    by_page: dict[int, list[RewriteSlot]] = {}
    for slot in slots:
        cleaned = strip_watermark(slot)
        if cleaned is not None:
            if cleaned == "":
                plan.blank_targets.add(slot.target)
            else:
                plan.text_ops.append(SetTextOp(target=slot.target, text=cleaned))
        page = page_of_target(slot.target)
        if page is not None:
            by_page.setdefault(page, []).append(slot)
    for page, page_slots in by_page.items():
        with_text = [slot for slot in page_slots if slot.text]
        if with_text and all(strip_watermark(slot) == "" for slot in with_text):
            plan.empty_slides.append(page)
    return plan


def pick_deletions(slide_count: int, junk: Iterable[int], planned: Iterable[int]) -> set[int]:
    """合并废页与模型判删的页。

    废页优先排在前面，删页下限是至少留一页——空演示文稿打不开，也不如如实告诉用户没删。
    """
    out: set[int] = set()
    for index in [*junk, *planned]:
        if len(out) >= slide_count - 1:
            break
        if index < 0 or index >= slide_count:
            continue
        out.add(index)
    return out


def page_digest(structure: TemplateStructure) -> list[PageDigest]:
    """每页摘要供页规划用：文本槽数与截断后的正文，控制在一次调用的预算内。"""
    out: list[PageDigest] = []
    for slide in structure.slides:
        texts = [shape for shape in slide.shapes if shape.kind in WRITABLE_KINDS and shape.text]
        joined = "　".join(_FUZZ_BLANK.sub(" ", shape.text).strip() for shape in texts)
        out.append(PageDigest(slide=slide.index, slots=len(texts), text=joined[:160]))
    return out


def clamp_to_limit(text: str, limit: int) -> str:
    """超出该槽预算时按句读收口，砍在句子中间比少一句更难看。

    前一半都没有句读就硬切，避免只留下几个字的碎片。
    """
    normalized = _LINE_BREAKS.sub("\n", text)
    if len(normalized) <= limit:
        return normalized
    cut = normalized[:limit]
    hard = [cut.rfind(punctuation) + 1 for punctuation in HARD_STOPS]
    soft = [
        position
        for position in (cut.rfind(punctuation) + 1 for punctuation in SOFT_STOPS)
        if position > limit / 2
    ]
    stops = [value for value in [cut.rfind("\n"), *hard, *soft] if value > 0]
    if not stops:
        return cut
    return _TRAILING_PUNCT.sub("", cut[: max(stops)])


def set_text_ops(raw: Iterable[Any]) -> list[SetTextOp]:
    """归一模型产出的文本指令：丢弃非 set_text 与没有 target 的条目，text 一律转字符串。

    模型会吐出白名单外的动作、缺字段的对象，这里按运行时值拒绝，不指望它守约。
    """
    ops: list[SetTextOp] = []
    for item in raw:
        if not isinstance(item, Mapping) or item.get("op") != "set_text":
            continue
        target = item.get("target")
        if not isinstance(target, str):
            continue
        value = item.get("text")
        ops.append(SetTextOp(target=target, text="" if value is None else str(value)))
    return ops


def delete_slide_indices(raw: Iterable[Any]) -> list[int]:
    """从模型回的数组里只收整数页码。

    空串会被 `int()` 当成 0，等于误删第一页，必须先挡掉；null、布尔之类一律丢弃。
    """
    out: list[int] = []
    for value in raw:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            out.append(value)
        elif isinstance(value, str) and value.strip():
            try:
                out.append(int(value))
            except ValueError:
                continue
    return out


def dedupe_ops(ops: Iterable[SetTextOp]) -> list[SetTextOp]:
    """同一槽只留最后一条指令：自检阶段清空与补写谁说了算，取决于传参顺序。"""
    by_target: dict[str, SetTextOp] = {}
    for op in ops:
        by_target[op.target] = op
    return list(by_target.values())


def batch_slots_by_page[T: _HasTarget](
    slots: Sequence[T], pages_per_batch: int = REWRITE_PAGES_PER_BATCH
) -> list[list[T]]:
    """按页切批：同一页的槽位必须落在同一批，每批最多 pages_per_batch 页，避免单次输出超预算。"""
    by_page: dict[int, list[T]] = {}
    for slot in slots:
        page = page_of_target(slot.target)
        if page is None:
            continue
        by_page.setdefault(page, []).append(slot)
    pages = sorted(by_page)
    return [
        [slot for page in pages[start : start + pages_per_batch] for slot in by_page[page]]
        for start in range(0, len(pages), pages_per_batch)
    ]


__all__ = [
    "FURNITURE_PH",
    "PAGE_PLAN_BATCH",
    "PLAN_CONCURRENCY",
    "REWRITE_CONCURRENCY",
    "REWRITE_PAGES_PER_BATCH",
    "WRITABLE_KINDS",
    "PageDigest",
    "RewriteSlot",
    "WatermarkPlan",
    "batch_slots_by_page",
    "clamp_to_limit",
    "count_unwritable_texts",
    "dedupe_ops",
    "delete_slide_indices",
    "find_echoed_slots",
    "is_watermark_line",
    "page_digest",
    "page_of_target",
    "pick_deletions",
    "plan_watermark_cleaning",
    "set_text_ops",
    "slot_text_budget",
    "stray_watermark_ops",
    "strip_watermark",
    "template_text_map",
    "text_slots",
]
