"""Word 导入生成模板草稿。

主路径是表格的确定性解析：docx 里的表格本来就是确定数据，`gridSpan` / `vMerge` 直接给出
横向宽度和纵向贯穿，让模型去猜结构正是以前"导入不准、反复重建也达不到目标"的根源。
只有解析不出像样结构（没有表格的纯段落文档）才回退模型识别。

全程不落库、原文件也不保存：返回草稿给前端填进模板编辑器，教师确认后走正常的新建接口。
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from secrets import token_hex
from typing import Annotated, Any, get_args

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from fastapi import Depends
from langchain_core.messages import HumanMessage, SystemMessage

from ailan.ai_chat.config_service import AssistantConfigService, AssistantConfigServiceDep
from ailan.core.config import settings
from ailan.core.errors import BadRequestError
from ailan.llm.factory import build_model_with_fallback
from ailan.llm.text import empty_content_hint, model_text, pick_json_object
from ailan.templates.schemas import (
    HALF_WIDTH_DEFAULT_IDS,
    FieldType,
    FormField,
    TemplateDraft,
    normalize_width,
)

# 文档结构进入模型的最大字符数：模板栏目集中在开头，超长截断
OUTLINE_CHAR_LIMIT = 12000
# 一次导入最多保留的字段数，防止模型把整篇正文逐段建成字段
MAX_IMPORT_FIELDS = 40
# 确定性解析至少认出这么多个字段才算成功，否则视为文档不是表格结构、回退模型识别
MIN_TABLE_FIELDS = 3
# 上传的 Word 上限：模板文档一般几百 KB，20MB 足够宽，再大就当异常拒绝
DOCX_SIZE_LIMIT = 20 * 1024 * 1024

LABEL_MAX = 30
NAME_MAX = 40
OPTION_MAX = 30
OPTION_COUNT_MAX = 20

FIELD_TYPES: frozenset[FieldType] = frozenset(get_args(FieldType))
NUMBER_LABEL_PATTERN = re.compile(r"课时|学时|分钟")
WHITESPACE = re.compile(r"\s+")
HEADING_STYLE = re.compile(r"^heading\s*([1-6])$", re.IGNORECASE)
DOCX_SUFFIX = re.compile(r"\.docx?$", re.IGNORECASE)

# 常见栏目名 → 基础四项的固定 id。必须落到固定 id：教案新建按课题名回填、导出取标题、
# 半宽缺省都认 id；认不出来的栏目按普通字段处理，编辑器里仍可改标签
BASIC_ID_BY_LABEL = {
    "课题": "basic-topic",
    "课题名称": "basic-topic",
    "课程名称": "basic-topic",
    "课文题目": "basic-topic",
    "单元": "basic-unit",
    "单元名称": "basic-unit",
    "学科": "basic-subject",
    "科目": "basic-subject",
    "年级": "basic-grade",
}

SYSTEM_PROMPT = "\n".join(
    [
        "你在把一份 Word 教学文档（教案、教学设计、导学案之类）转成表单模板，供教师在平台上按模板填写教案。",
        "给你的是文档结构：# 开头是各级标题，| 开头是表格的一行（单元格用 | 分隔，"
        "[跨N] 表示该格横向盖 N 列、[纵N] 表示纵向盖 N 行），其余是正文段落。",
        "规则：",
        "- 字段只取文档里真实出现的栏目（表头、各级标题、「名称：」这类标签），"
        "不得增删教学环节、不得编造文档里没有的栏目。",
        "- type 判定：一两句就能填的短信息用 input；需要成段书写（目标、过程、反思、作业）用 textarea；",
        "  文档里明显给出固定候选项的用 select 并带上 options；课时、分钟这类数字用 number；",
        "  只起分组作用、下面套着其他栏目的标题（标题套标题，本身不需要填写）用 label。",
        "- 标题套标题的识别：表格某行只有一个单元格或一格 [跨N] 盖住整行、下面几行才是具体栏目，"
        "那一行就是分组标题，必须用 label，不许建成 input/textarea；",
        "  带 [纵N] 的小格子（比如行首只有四五个字、纵向罩住 N 行具体栏目）是这一组栏目的竖排大标题，"
        "也是 label，并且必须给它 rowSpan=N（它纵向贯穿几行）；",
        "  标题层级可以套多层（大标题 label 下面还有小标题 label，小标题下面才是填写项），"
        "按文档出现的顺序原样排成多个字段即可。",
        "- 基础四项（课题/单元/学科/年级）若存在，id 必须分别用 basic-topic / basic-unit / "
        "basic-subject / basic-grade；其他字段不要给 id。",
        "- width 从 1/12 到 1 按 1/12 递增共十二档取（1/12、1/6、1/4、1/3、5/12、1/2、7/12、2/3、3/4、"
        "5/6、11/12、1）：短信息项给 1/2（两项并排）或更窄，整段正文给 1；同一行的字段宽度加起来不要超过 1。",
        "- label 的 width：整行盖满的分组标题给 1；文档里那种只有一两个字宽、文字竖着排的行首标题给 1/12"
        "（导出一字一行，正是竖排效果）。",
        "- rowSpan 只对 label 有意义，等于 [纵N] 里的 N（2-12）；"
        "竖排大标题不需要 rows，高度由它贯穿的行决定。",
        "- labelPosition：短栏目与内容左右并排用 left；正文型长栏目标签放在内容上方用 top；"
        "那一格文档里确实没有栏目名、只有内容的用 none；label 类型不需要 labelPosition。",
        "- 多行文本（textarea）给 rows（高度，单位是行），2-20 之间按篇幅判断：正文一般 4、较长 8、最长 12。",
        "- name 用文档标题或第一个一级标题；没有就用「导入的模板」。",
        "只返回 JSON，不要任何解释文字：",
        '{"name":"...","fields":[{"label":"课题","id":"basic-topic","type":"input","width":0.5,'
        '"labelPosition":"left"},',
        '{"label":"课时教学设计","type":"label","width":1},',
        '{"label":"学习目标定位","type":"label","width":0.08333333333333333,"rowSpan":6},',
        '{"label":"教学目标","type":"textarea","width":1,"labelPosition":"top","rows":12,'
        '"placeholder":"可选的填写提示"}]}',
    ]
)


def _one_line(value: str) -> str:
    return WHITESPACE.sub(" ", value).strip()


def _new_field_id(seq: int) -> str:
    """字段 id 只要唯一就行，不需要可读：编辑器里它是 key，导出时靠 label 出表头。"""
    return f"field-{token_hex(4)}-{seq}"


def open_docx(data: bytes) -> DocxDocument:
    try:
        return Document(io.BytesIO(data))
    except Exception as exc:
        raise BadRequestError(f"Word 文件无法解析：{exc}（老式 .doc 请先另存为 .docx）") from exc


def body_blocks(document: DocxDocument) -> Iterator[Paragraph | Table]:
    """按文档顺序产出顶层段落与表格——模板名在"第一张表格之前"，顺序不能丢。"""
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


@dataclass(frozen=True)
class GridCell:
    """表格网格里解析完合并之后的一个单元格：起始行列 + 横向盖几列、纵向罩几行。"""

    text: str
    row: int
    col: int
    col_span: int
    row_span: int

    @property
    def right(self) -> int:
        return self.col + self.col_span

    @property
    def bottom(self) -> int:
        return self.row + self.row_span


def _grid_span(tc: Any) -> int:
    """`<w:gridSpan w:val="3"/>`：这格横向盖住三列网格。"""
    pr = tc.find(qn("w:tcPr"))
    node = pr.find(qn("w:gridSpan")) if pr is not None else None
    if node is None:
        return 1
    try:
        return max(1, int(node.get(qn("w:val"))))
    except (TypeError, ValueError):
        return 1


def _is_merge_continuation(tc: Any) -> bool:
    """纵向合并的续格（`<w:vMerge/>`，没有 val 或 val 不是 restart）：内容都在起始格上。

    把它当成一格会凭空多出一个空栏目，所以要跳过——但它照样占网格列，列号仍然要往后推。
    """
    pr = tc.find(qn("w:tcPr"))
    node = pr.find(qn("w:vMerge")) if pr is not None else None
    if node is None:
        return False
    return (node.get(qn("w:val")) or "continue").lower() != "restart"


def build_grid(table: Table) -> tuple[list[GridCell], int]:
    """把一张表摊成带坐标的网格，并算出每格的横向列数与纵向行数。"""
    raw_rows: list[dict[int, tuple[str, int, bool]]] = []
    columns = 0
    for tr in table._tbl.tr_lst:
        row: dict[int, tuple[str, int, bool]] = {}
        col = 0
        for tc in tr.tc_lst:
            span = _grid_span(tc)
            row[col] = (_one_line(_Cell(tc, table).text), span, _is_merge_continuation(tc))
            col += span
        columns = max(columns, col)
        raw_rows.append(row)

    cells: list[GridCell] = []
    for row_index, row in enumerate(raw_rows):
        for col, (text, span, continues) in sorted(row.items()):
            if continues:
                continue
            row_span = 1
            below = row_index + 1
            while below < len(raw_rows):
                piece = raw_rows[below].get(col)
                if piece is None or not piece[2]:
                    break
                row_span += 1
                below += 1
            cells.append(GridCell(text=text, row=row_index, col=col, col_span=span, row_span=row_span))
    cells.sort(key=lambda cell: (cell.row, cell.col))
    return cells, columns


@dataclass
class _Item:
    """一个候选字段，连同它在哪一行、占几个网格列，供后面按行压宽度。"""

    field: FormField
    row: int
    tw: int
    cover_to: int
    basic: bool


def _tw_of(width_in_columns: int, columns: int) -> int:
    """网格列数换算成 12 档里的份数，至少占一档。"""
    return max(1, round(width_in_columns * 12 / columns))


def _fields_from_table(cells: list[GridCell], columns: int, new_id: Callable[[], str]) -> list[_Item]:
    """一张表 → 一组字段。规则：

    - 纵向合并的文字格 → 纯标签，rowSpan = 它罩住的"有内容的行"数（空白填写行并进上面的字段，不算行）
    - 文字格 + 右边/下边的空格 → 一个填写项：宽度 = 合并后的列占比，标签在上/在左由空格方位决定
    """
    lookup = {(cell.row, cell.col): cell for cell in cells}
    content_rows = {cell.row for cell in cells if cell.text}
    used_basic_ids: set[str] = set()
    items: list[_Item] = []

    for cell in cells:
        if not cell.text:
            continue
        label = cell.text[:LABEL_MAX]
        basic_id = BASIC_ID_BY_LABEL.get(cell.text.replace(" ", ""))
        if basic_id and basic_id in used_basic_ids:
            continue
        field_id = basic_id or new_id()
        if basic_id:
            used_basic_ids.add(basic_id)

        if cell.row_span > 1:
            spanned = sum(1 for row in content_rows if cell.row <= row < cell.bottom)
            imported = FormField(
                id=field_id, type="label", label=label, width=_tw_of(cell.col_span, columns) / 12
            )
            if spanned >= 2:
                imported = imported.model_copy(update={"row_span": min(12, spanned)})
            items.append(
                _Item(
                    field=imported,
                    row=cell.row,
                    tw=_tw_of(cell.col_span, columns),
                    cover_to=cell.bottom - 1,
                    basic=bool(basic_id),
                )
            )
            continue

        # 右边的空格 → 本行的横向填写区；下边的空格 → 纵向填写区（可以连好几行）
        right_blank = 0
        cursor = cell.right
        while True:
            neighbor = lookup.get((cell.row, cursor))
            if neighbor is None or neighbor.text:
                break
            right_blank += neighbor.col_span
            cursor += neighbor.col_span
        area_width = cell.col_span + right_blank

        depth = 0
        row_below = cell.row + 1
        # 只算"整行没有文字"的空白续行：下方栏目自带的行也有空白，但那是下面字段的填写区，
        # 不能向上被算进本字段的高度
        while row_below not in content_rows:
            spanning = [
                other
                for other in cells
                if other.row == row_below
                and not other.text
                and other.col <= cell.col
                and other.right >= cell.col + area_width
            ]
            if not spanning:
                break
            depth += 1
            row_below += 1

        tw = _tw_of(area_width, columns)
        if field_id == "basic-topic":
            field_type = "input"
        elif NUMBER_LABEL_PATTERN.search(label):
            field_type = "number"
        elif depth >= 1 or tw >= 8:
            field_type = "textarea"
        elif right_blank > 0:
            field_type = "input"
        else:
            field_type = "label"

        updates: dict[str, Any] = {}
        if field_type in ("textarea", "input", "number"):
            if depth >= 1:
                updates["label_position"] = "top"
            elif right_blank > 0:
                updates["label_position"] = "left"
        if field_type == "textarea":
            updates["rows"] = min(20, (1 + depth) * 2 if depth >= 1 else 3)

        items.append(
            _Item(
                field=FormField(id=field_id, type=field_type, label=label, width=tw / 12, **updates),
                row=cell.row,
                tw=tw,
                cover_to=cell.row,
                basic=bool(basic_id),
            )
        )

    _pack_rows_into_twelve(items, content_rows)
    return items


def _pack_rows_into_twelve(items: list[_Item], content_rows: set[int]) -> None:
    """每行合计压进 12 列：先收缩本行最宽的非基础填写项，保住基础项与标签的原始占比。

    按行号升序处理：贯穿标签会罩住后面几行，先处理上一行才能在下一行把超出的宽度压掉。
    """
    for row in sorted(content_rows):
        active = [item for item in items if item.row <= row <= item.cover_to]
        starting = [item for item in items if item.row == row]
        total = sum(item.tw for item in active)
        while total > 12:
            victims = [
                item for item in starting if item.tw > 1 and item.field.type != "label" and not item.basic
            ]
            pool = victims or [item for item in starting if item.tw > 1 and item.field.type != "label"]
            if not pool:
                break
            max(pool, key=lambda item: item.tw).tw -= 1
            total = sum(item.tw for item in active)
    for item in items:
        item.field.width = item.tw / 12


def docx_to_fields(data: bytes) -> tuple[str | None, list[FormField]]:
    """表格 → 字段的确定性解析；认不出结构时返回空 fields，由调用方回退模型识别。"""
    document = open_docx(data)
    fields: list[FormField] = []
    seq = 0

    def new_id() -> str:
        nonlocal seq
        field_id = _new_field_id(seq)
        seq += 1
        return field_id

    name = _document_name(document)
    for block in body_blocks(document):
        if not isinstance(block, Table):
            continue
        cells, columns = build_grid(block)
        if not cells or columns < 2:
            continue
        items = _fields_from_table(cells, columns, new_id)
        # 源文档里的每张表格都是一段独立结构：从第二张起前面补一个分隔符，
        # 导出时才不会把两张表并回一张
        if fields:
            fields.append(FormField(id=new_id(), type="divider", label="分隔符"))
        fields.extend(item.field for item in items)
    return name, fields[:MAX_IMPORT_FIELDS]


def _document_name(document: DocxDocument) -> str | None:
    """模板名取第一张表格之前的第一个非空段落。"""
    for block in body_blocks(document):
        if isinstance(block, Table):
            return None
        text = _one_line(block.text)
        if text:
            return text[:NAME_MAX]
    return None


def docx_to_outline(data: bytes) -> str:
    """docx → 结构文本：保留标题层级、表格行列与段落，模型据此判断栏目。"""
    document = open_docx(data)
    lines: list[str] = []
    for block in body_blocks(document):
        if isinstance(block, Table):
            lines.extend(_outline_table(block))
            continue
        text = _one_line(block.text)
        if not text:
            continue
        style = (block.style.name or "") if block.style is not None else ""
        heading = HEADING_STYLE.match(style)
        if heading:
            lines.append(f"{'#' * int(heading.group(1))} {text}")
        elif style.lower().startswith("list"):
            lines.append(f"- {text}")
        else:
            lines.append(text)
    return "\n".join(lines)[:OUTLINE_CHAR_LIMIT]


def _outline_table(table: Table) -> list[str]:
    cells, _ = build_grid(table)
    lines: list[str] = []
    for row_index in sorted({cell.row for cell in cells}):
        pieces: list[str] = []
        for cell in cells:
            if cell.row != row_index or not cell.text:
                continue
            marker = ""
            if cell.col_span > 1:
                marker += f"[跨{cell.col_span}]"
            if cell.row_span > 1:
                marker += f"[纵{cell.row_span}]"
            pieces.append(f"{cell.text}{marker}")
        if pieces:
            lines.append(f"| {' | '.join(pieces)}")
    return lines


def normalize_imported_fields(raw: Any) -> list[FormField]:
    """模型产出的字段是不可信输入，逐个归一化：

    标签去空、类型限定在白名单、宽度按档位归一、下拉没候选就退回单行输入、rows 限定 2-20。
    """
    if not isinstance(raw, list):
        return []

    fields: list[FormField] = []
    used_basic_ids: set[str] = set()
    seq = 0

    for item in raw[:MAX_IMPORT_FIELDS]:
        if not isinstance(item, dict):
            continue
        label = _one_line(str(item.get("label") or ""))[:LABEL_MAX]
        if not label:
            continue

        basic_id = BASIC_ID_BY_LABEL.get(label.replace(" ", ""))
        # 同一栏目在文档里重复出现（表头跨页最常见）只留第一个
        if basic_id and basic_id in used_basic_ids:
            continue
        field_id = basic_id or _new_field_id(seq)
        seq += 1
        if basic_id:
            used_basic_ids.add(basic_id)

        declared = str(item.get("type") or "")
        # 认不出来的类型统一按 input 建：文档里手写了一个没见过的词，不该把整个字段丢掉
        field_type: FieldType = declared if declared in FIELD_TYPES else "input"

        updates: dict[str, Any] = {
            "width": normalize_width(item.get("width"), 0.5 if field_id in HALF_WIDTH_DEFAULT_IDS else 1.0)
        }
        if field_type == "select":
            declared = item.get("options")
            raw_options = declared if isinstance(declared, list) else []
            options = list(
                dict.fromkeys(
                    option
                    for option in (_one_line(str(option or ""))[:OPTION_MAX] for option in raw_options)
                    if option
                )
            )[:OPTION_COUNT_MAX]
            # 下拉没有候选就没法用，退回单行输入
            if len(options) < 2:
                field_type = "input"
            else:
                updates["options"] = options

        position = item.get("labelPosition") or item.get("label_position")
        if position in ("top", "none"):
            updates["label_position"] = position
        # rows 是多行文本和纯标题共用的高度（标题单元格靠它撑出竖排/留白）
        if field_type in ("textarea", "label"):
            rows = _bounded_int(item.get("rows"), 2, 20)
            if rows is not None:
                updates["rows"] = rows
        # 竖排贯穿大标题：只有 label 有 rowSpan，2-12 之外按不贯穿处理
        if field_type == "label":
            row_span = _bounded_int(item.get("rowSpan") or item.get("row_span"), 2, 12)
            if row_span is not None:
                updates["row_span"] = row_span
        placeholder = _one_line(str(item.get("placeholder") or ""))[:LABEL_MAX]
        if placeholder:
            updates["placeholder"] = placeholder

        fields.append(FormField(id=field_id, type=field_type, label=label, **updates))

    return fields


def _bounded_int(value: Any, low: int, high: int) -> int | None:
    try:
        # round() 不带第二个参数就已经给出整数；NaN 落在 ValueError，±inf 落在 OverflowError
        number = round(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if low <= number <= high else None


class TemplateImportService:
    def __init__(self, configs: AssistantConfigService) -> None:
        self.configs = configs

    async def parse_draft(self, data: bytes, file_name: str | None = None) -> TemplateDraft:
        """上传的 docx → 模板草稿（不落库；确认前用户可在编辑器里随意调整）。"""
        deterministic = self._table_draft(data, file_name)
        if deterministic is not None:
            return deterministic

        outline = docx_to_outline(data)
        if not outline.strip():
            raise BadRequestError("这份 Word 里没有可识别的文字内容")

        spec = await self.configs.spec_or_throw()
        # 思考型模型会先把输出预算烧在推理上：识别嵌套标签时推理能烧掉近 1.5 万 token，
        # 预算取用户配置与全局下限的较大者，否则正文会被吃空
        model = build_model_with_fallback(
            spec,
            streaming=False,
            max_tokens=max(spec.max_tokens, settings.llm_structured_max_tokens),
            thinking=False,
        )
        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=f"【文档结构】\n{outline}")]
        last_error = "模型没有返回内容"

        # 结构化输出偶尔会写成散文或被截断，再问一次；两次都不成才报错，把原因如实带回去
        for _ in range(2):
            try:
                response = await model.ainvoke(messages)
                text = model_text(response)
                if not text.strip():
                    last_error = empty_content_hint(response)
                    continue
                draft = pick_json_object(text)
                fields = normalize_imported_fields(draft.get("fields") if draft else None)
                if not fields:
                    last_error = "没有识别出任何栏目"
                    continue
                name = _one_line(str((draft or {}).get("name") or ""))[:NAME_MAX]
                return TemplateDraft(name=name or "导入的模板", fields=fields)
            except BadRequestError:
                raise
            except Exception as exc:
                last_error = str(exc)

        raise BadRequestError(
            f"没能从这份 Word 里识别出模板结构（{last_error[:80]}）。文档若为图片扫描件，请改用手工新建。"
        )

    def _table_draft(self, data: bytes, file_name: str | None) -> TemplateDraft | None:
        """确定性草稿：解析失败、没有表格或字段太少都返回 None，由调用方回退模型识别。

        这里解析失败不抛错——报错文案统一交给回退路径，保持前后一致。
        """
        try:
            name, fields = docx_to_fields(data)
        except BadRequestError:
            return None
        if len(fields) < MIN_TABLE_FIELDS:
            return None
        stem = _one_line(DOCX_SUFFIX.sub("", file_name or ""))[:NAME_MAX]
        return TemplateDraft(name=name or stem or "Word 导入模板", fields=fields)


def get_template_import_service(configs: AssistantConfigServiceDep) -> TemplateImportService:
    return TemplateImportService(configs)


TemplateImportServiceDep = Annotated[TemplateImportService, Depends(get_template_import_service)]
