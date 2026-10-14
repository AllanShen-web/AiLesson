"""pptx 模板外壳的 OOXML 读写层。

`python-pptx` 覆盖的是"从零新建一份演示文稿"，而导入外部模板的诉求是保住原排版、配色与母版，
只把框里的文字换掉、把多余的页删掉——这件事必须直接动包里的 XML，所以这一层是那把螺丝刀。
它只做四件事：解析形状清单、按指令写回文本、删整页、剥母版层水印，
不认识"课件"这个概念，也不碰数据库，纯进纯出，好测。

标签一律按命名空间 URI 匹配：`a:` / `p:` 前缀只是惯例，各家工具导出的模板前缀不一定照抄，
但 URI 是规范里写死的。
"""

from __future__ import annotations

import copy
import re
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import lxml.etree as etree

# 白名单与页序解析与槽位层共用同一份实现：解析、挑槽、回写三处口径必须一致
from ailan.ppt.schemas import SetTextOp, TemplateShape, TemplateSlide, TemplateStructure
from ailan.ppt.slots import WRITABLE_KINDS, page_of_target

NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _a(local: str) -> str:
    return f"{{{NS_A}}}{local}"


def _p(local: str) -> str:
    return f"{{{NS_P}}}{local}"


def _r(local: str) -> str:
    return f"{{{NS_R}}}{local}"


# 热点标签预先拼好，省掉每次调用都在拼字符串
A_P = _a("p")
A_R = _a("r")
A_T = _a("t")
A_BR = _a("br")
A_TAB = _a("tab")
A_PR = _a("rPr")
A_PPR = _a("pPr")
A_END_PR = _a("endParaRPr")
A_XFRM = _a("xfrm")
A_OFF = _a("off")
A_EXT = _a("ext")
A_TBL = _a("tbl")
A_TR = _a("tr")
A_TC = _a("tc")
A_TXBODY = _a("txBody")
A_GRAPHIC_DATA = _a("graphicData")
A_HLINK_CLICK = _a("hlinkClick")
A_HLINK_HOVER = _a("hlinkHover")
P_SP = _p("sp")
P_PIC = _p("pic")
P_GRAPHIC_FRAME = _p("graphicFrame")
P_CXN_SP = _p("cxnSp")
P_GRP_SP = _p("grpSp")
P_CN_V_PR = _p("cNvPr")
P_PH = _p("ph")
RELATIONSHIP = f"{{{NS_PKG}}}Relationship"

# 深度遍历时需要登记的形状容器；组合自身也算，组里的文本框同样可改写
SHAPE_TAGS = (P_SP, P_PIC, P_GRAPHIC_FRAME, P_CXN_SP)
# 从 cNvPr 上溯定位形状元素时，这几类才算形状自身
SHAPE_ELEMENTS = (*SHAPE_TAGS, P_GRP_SP)

EMU_PER_INCH = 914400
_SHELL_PART = re.compile(r"^ppt/slide(?:Layouts|Masters)/[^/]+\.xml$")


class _SkipReason:
    """回写阶段的一手原因码，前端不展示，只进日志与报告计数。"""

    SHAPE_NOT_FOUND = "shape_not_found"
    SLIDE_DELETED = "slide_deleted"
    NOT_TEXT = "not_text"
    SLIDE_FILE_MISSING = "slide_file_missing"


@dataclass
class Skip:
    target: str | None
    reason: str


@dataclass
class ApplyResult:
    """一次回写的执行结果：写成了几个框、跳过了哪些、删了哪几页。"""

    applied: int = 0
    skipped: list[Skip] = field(default_factory=list)
    deleted_slides: list[int] = field(default_factory=list)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


@dataclass
class _Job:
    """一条已定位到部件的写回任务：kind 决定走文本框还是表格入口。"""

    target: str
    shape_id: int
    kind: str
    text: str


@dataclass
class _Shape:
    shape_id: int
    name: str
    kind: str
    placeholder_type: str | None
    placeholder_index: int | None
    text: str
    left: float | None
    top: float | None
    width: float | None
    height: float | None


@dataclass
class _Slide:
    index: int
    path: str
    notes: str
    shapes: list[_Shape]

    @property
    def rels_path(self) -> str:
        return rels_path_for(self.path)


class _Package:
    """pptx 的部件表。

    解析阶段全量读进内存，改写只在字典里动，最后一次整包重打——
    zip 不支持原地替换条目，边改边写反而更容易留下半改状态。
    """

    def __init__(self, data: bytes) -> None:
        self.entries: dict[str, bytes] = {}
        with zipfile.ZipFile(BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                self.entries[info.filename] = archive.read(info.filename)

    def has(self, name: str) -> bool:
        return name in self.entries

    def xml(self, name: str) -> Any:
        raw = self.entries.get(name)
        if raw is None:
            return None
        return etree.fromstring(raw)

    def set_xml(self, name: str, root: Any) -> None:
        # standalone 声明不能丢：部分工具严格按 OOXML 的包约定校验它
        self.entries[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def remove(self, name: str) -> None:
        self.entries.pop(name, None)

    def write(self) -> bytes:
        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, raw in self.entries.items():
                archive.writestr(name, raw)
        return out.getvalue()


# ── 定位与读取 ──


def _inch(value: float) -> float:
    return round(value / EMU_PER_INCH, 3)


def _kids(node: Any, tag: str) -> list[Any]:
    return [child for child in node if child.tag == tag]


def _first(node: Any, tag: str) -> Any:
    for child in node:
        if child.tag == tag:
            return child
    return None


def _first_of(node: Any, *tags: str) -> Any:
    """按顺序取首个命中的子节点。

    lxml 元素的真假取决于有没有子节点，空元素是 False，所以这里必须显式比 None，
    不能写成 `a or b`。
    """
    for tag in tags:
        found = _first(node, tag)
        if found is not None:
            return found
    return None


def _first_element(node: Any) -> Any:
    """首个元素子节点（跳过注释与处理指令）。"""
    for child in node:
        if isinstance(child.tag, str):
            return child
    return None


def _int_attr(node: Any, name: str) -> int | None:
    raw = node.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _resolve_relative(base_dir: str, target: str) -> str:
    stack = [part for part in base_dir.split("/") if part]
    for part in target.split("/"):
        if part == "..":
            if stack:
                stack.pop()
        elif part in (".", ""):
            continue
        else:
            stack.append(part)
    return "/".join(stack)


def part_path(base_dir: str, target: str) -> str:
    """关系 Target 解析：相对当前部件，或以 / 开头的包内绝对路径。"""
    if not target:
        return ""
    if target.startswith("/"):
        return target[1:]
    return _resolve_relative(base_dir, target)


def rels_path_for(part: str) -> str:
    """部件对应的关系文件：ppt/slides/slide2.xml → ppt/slides/_rels/slide2.xml.rels"""
    head, _, tail = part.rpartition("/")
    return f"{head}/_rels/{tail}.rels"


def _read_position(xfrm: Any) -> tuple[float | None, float | None, float | None, float | None]:
    if xfrm is None:
        return (None, None, None, None)
    off = _first(xfrm, A_OFF)
    ext = _first(xfrm, A_EXT)

    def attr(node: Any, name: str) -> float | None:
        if node is None:
            return None
        raw = node.get(name)
        if raw is None:
            return None
        try:
            return _inch(float(raw))
        except ValueError:
            return None

    return (attr(off, "x"), attr(off, "y"), attr(ext, "cx"), attr(ext, "cy"))


def _paragraph_text(paragraph: Any) -> str:
    """段落文本。

    软换行读成 `\\v`、制表符读成 `\\t`，与换段用的 `\\n` 区分开，
    三种分隔符互不冲突，改写指令才能原样落回原来的结构。
    """
    out: list[str] = []
    for node in paragraph:
        if node.tag in (A_R, _a("fld")):
            out.append("".join(t.text or "" for t in node.iter(A_T)))
        elif node.tag == A_BR:
            out.append("\v")
        elif node.tag == A_TAB:
            out.append("\t")
    return "".join(out)


def _tx_body_text(tx_body: Any) -> str:
    if tx_body is None:
        return ""
    return "\n".join(_paragraph_text(p) for p in _kids(tx_body, A_P))


def _table_text(tbl: Any) -> str:
    """表格文本读出：行用 `\\n`、列用 `\\t`，格内多段落用 `\\v`。"""
    rows: list[str] = []
    for tr in _kids(tbl, A_TR):
        cells: list[str] = []
        for tc in _kids(tr, A_TC):
            cells.append(_tx_body_text(_first(tc, A_TXBODY)).replace("\n", "\v"))
        rows.append("\t".join(cells))
    return "\n".join(rows)


_PH_TYPE_MAP = {
    "ctrTitle": "CENTER_TITLE",
    "title": "TITLE",
    "subTitle": "SUBTITLE",
    "body": "BODY",
    "obj": "OBJECT",
    "pic": "PICTURE",
    "chart": "CHART",
    "tbl": "TABLE",
    "media": "MEDIA_CLIP",
    "dt": "DATE",
    "ftr": "FOOTER",
    "sldNum": "SLIDE_NUMBER",
    "hdr": "HEADER",
}


def _read_ph(nv_pr: Any) -> tuple[str, int | None] | None:
    """解析 `p:ph`，返回占位符类型名与 idx。

    类型名统一成大写枚举风格，与存量 structure 数据的口径一致。
    """
    if nv_pr is None:
        return None
    ph = _first(nv_pr, P_PH)
    if ph is None:
        return None
    raw_type = str(ph.get("type") or "")
    idx = _int_attr(ph, "idx")
    type_name: str
    if raw_type:
        type_name = _PH_TYPE_MAP.get(raw_type, raw_type.upper())
    elif idx == 0:
        type_name = "TITLE"
    elif idx == 1:
        type_name = "BODY"
    else:
        type_name = "OBJECT"
    return (type_name, idx)


def _collect_shape_nodes(node: Any) -> list[Any]:
    """深度遍历形状树：组自身与组内后代都登记，组合里的文本框才可被解析与回写。

    组内子形状的 xfrm 处于组的 child space，此处按形状自身 xfrm 直读（不叠加组变换），
    位置字段只作存档，改写链路不消费它。
    """
    out: list[Any] = []
    for child in node:
        if child.tag == P_GRP_SP:
            out.append(child)
            out.extend(_collect_shape_nodes(child))
        elif child.tag in SHAPE_TAGS:
            out.append(child)
    return out


def _slide_paths(package: _Package) -> list[str]:
    """按 sldIdLst 顺序取页面部件路径。

    只取根下 sldIdLst 的直接子项：分栏里也有 `p:sldId`，但它按数值引用、不带 r:id。
    页序即 index，缺件必须抛错而不是静默错位。
    """
    pres = package.xml("ppt/presentation.xml")
    rels = package.xml("ppt/_rels/presentation.xml.rels")
    if pres is None or rels is None:
        raise ValueError("不是有效的 PPTX：缺少演示文稿部件")
    rel_map = {r.get("Id"): r.get("Target") or "" for r in rels.iter(RELATIONSHIP)}
    paths: list[str] = []
    sld_id_lst = _first(pres, _p("sldIdLst"))
    for node in _kids(sld_id_lst, _p("sldId")) if sld_id_lst is not None else []:
        rid = node.get(_r("id"))
        path = part_path("ppt/", rel_map.get(rid or "", ""))
        if not path or not package.has(path):
            raise ValueError(f"slide_part_missing:{rid or 'unknown'}")
        paths.append(path)
    return paths


def _read_notes(package: _Package, slide_path: str) -> str:
    rels = package.xml(rels_path_for(slide_path))
    if rels is None:
        return ""
    notes_path = ""
    for rel in rels.iter(RELATIONSHIP):
        if "notesSlide" in (rel.get("Type") or ""):
            notes_path = part_path("ppt/slides/", rel.get("Target") or "")
            break
    if not notes_path:
        return ""
    notes = package.xml(notes_path)
    if notes is None:
        return ""
    shapes = list(notes.iter(P_SP))

    def ph_of(shape: Any) -> Any:
        return next(shape.iter(P_PH), None)

    body = next(
        (sp for sp in shapes if (p := ph_of(sp)) is not None and p.get("type") == "body"),
        None,
    )
    if body is None:
        body = next(
            (
                sp
                for sp in shapes
                if (p := ph_of(sp)) is not None and p.get("type") not in ("sldNum", "ftr", "dt", "hdr")
            ),
            None,
        )
    return _tx_body_text(_first(body, _p("txBody"))) if body is not None else ""


def _read_shape(node: Any) -> _Shape:
    """把一个形状元素读成清单条目。"""
    tag = node.tag
    text = ""
    kind = "shape"
    placeholder_type: str | None = None
    placeholder_index: int | None = None

    if tag == P_SP:
        nv = _first(node, _p("nvSpPr"))
        c_n_v_pr = _first(nv, P_CN_V_PR) if nv is not None else None
        ph = _read_ph(_first(nv, _p("nvPr")) if nv is not None else None)
        tx_body = _first(node, _p("txBody"))
        if ph is not None:
            kind = "placeholder"
            placeholder_type, placeholder_index = ph
        elif tx_body is not None:
            kind = "textbox"
        text = _tx_body_text(tx_body)
        sp_pr = _first(node, _p("spPr"))
        xfrm = _first(sp_pr, A_XFRM) if sp_pr is not None else None
    elif tag == P_PIC:
        nv = _first(node, _p("nvPicPr"))
        c_n_v_pr = _first(nv, P_CN_V_PR) if nv is not None else None
        kind = "picture"
        sp_pr = _first(node, _p("spPr"))
        xfrm = _first(sp_pr, A_XFRM) if sp_pr is not None else None
    elif tag == P_GRAPHIC_FRAME:
        nv = _first(node, _p("nvGraphicFramePr"))
        c_n_v_pr = _first(nv, P_CN_V_PR) if nv is not None else None
        graphic_data = next(node.iter(A_GRAPHIC_DATA), None)
        uri = (graphic_data.get("uri") or "") if graphic_data is not None else ""
        if "/table" in uri:
            tbl = _first(graphic_data, A_TBL) if graphic_data is not None else None
            if tbl is not None:
                kind = "table"
                text = _table_text(tbl)
        elif "/chart" in uri:
            kind = "chart"
        xfrm = _first_of(node, _p("xfrm"), A_XFRM)
    elif tag == P_CXN_SP:
        nv = _first(node, _p("nvCxnSpPr"))
        c_n_v_pr = _first(nv, P_CN_V_PR) if nv is not None else None
        sp_pr = _first(node, _p("spPr"))
        xfrm = _first(sp_pr, A_XFRM) if sp_pr is not None else None
    else:
        nv = _first(node, _p("nvGrpSpPr"))
        c_n_v_pr = _first(nv, P_CN_V_PR) if nv is not None else None
        kind = "group"
        grp_sp_pr = _first(node, _p("grpSpPr"))
        xfrm = _first(grp_sp_pr, A_XFRM) if grp_sp_pr is not None else None

    if c_n_v_pr is None:
        raise ValueError("形状缺少 cNvPr，无法定位")
    shape_id = int(c_n_v_pr.get("id") or 0)
    left, top, width, height = _read_position(xfrm)
    return _Shape(
        shape_id=shape_id,
        name=c_n_v_pr.get("name") or "",
        kind=kind,
        placeholder_type=placeholder_type,
        placeholder_index=placeholder_index,
        text=text,
        left=left,
        top=top,
        width=width,
        height=height,
    )


def _inspect(data: bytes) -> list[_Slide]:
    package = _Package(data)
    slides: list[_Slide] = []
    for index, path in enumerate(_slide_paths(package)):
        doc = package.xml(path)
        if doc is None:
            raise ValueError(f"slide_part_missing:{path}")
        sp_tree = next(doc.iter(_p("spTree")), None)
        if sp_tree is None:
            raise ValueError(f"slide_sp_tree_missing:{path}")
        shapes = [_read_shape(node) for node in _collect_shape_nodes(sp_tree)]
        slides.append(_Slide(index=index, path=path, notes=_read_notes(package, path), shapes=shapes))
    return slides


def parse_structure(data: bytes) -> TemplateStructure:
    """解析 pptx 字节，返回模板结构（落 `ppt.structure`，AI 改写的唯一依据）。"""
    slides = _inspect(data)
    return TemplateStructure(
        slide_count=len(slides),
        slides=[
            TemplateSlide(
                index=slide.index,
                notes=slide.notes,
                shapes=[
                    TemplateShape(
                        target=f"{slide.index}-{shape.shape_id}",
                        name=shape.name,
                        shape_id=shape.shape_id,
                        kind=shape.kind,
                        placeholder_type=shape.placeholder_type,
                        placeholder_index=shape.placeholder_index,
                        text=shape.text,
                        left=shape.left,
                        top=shape.top,
                        width=shape.width,
                        height=shape.height,
                    )
                    for shape in slide.shapes
                ],
            )
            for slide in slides
        ],
    )


# ── 写回 ──


def _write_tx_body(tx_body: Any, text: str) -> None:
    """段落重建主体：保留首 run 的 rPr 与首段 pPr，按 `\\n` 拆段，使模板字体版式不丢。"""
    paragraphs = _kids(tx_body, A_P)
    head = paragraphs[0] if paragraphs else None

    run_props = None
    paragraph_props = None
    if head is not None:
        run = _first(head, A_R)
        if run is None:
            # 首 run 可能嵌在更里一层（组/图形内联），版式模板照抄它的 rPr
            run = next(head.iter(A_R), None)
        if run is not None and (found := _first(run, A_PR)) is not None:
            run_props = copy.deepcopy(found)
        if (found := _first(head, A_PPR)) is not None:
            paragraph_props = copy.deepcopy(found)

    for paragraph in paragraphs[1:]:
        tx_body.remove(paragraph)
    if head is not None:
        for node in _kids(head, A_R) + _kids(head, A_BR) + _kids(head, _a("fld")):
            head.remove(node)

    lines = [""] if text == "" else text.split("\n")

    def build_run(line: str) -> Any:
        run = etree.Element(A_R)
        if run_props is not None:
            run.append(copy.deepcopy(run_props))
        node = etree.SubElement(run, A_T)
        node.text = line
        return run

    def fill(paragraph: Any, line: str) -> None:
        run = build_run(line)
        # CT_TextParagraph 要求 a:endParaRPr 只能是末子元素，run 必须插在它之前
        end_pr = _first(paragraph, A_END_PR)
        if end_pr is not None:
            end_pr.addprevious(run)
        else:
            paragraph.append(run)

    def apply_props(paragraph: Any) -> None:
        if (old := _first(paragraph, A_PPR)) is not None:
            paragraph.remove(old)
        if paragraph_props is not None:
            paragraph.insert(0, copy.deepcopy(paragraph_props))

    if head is None:
        head = etree.SubElement(tx_body, A_P)
    fill(head, lines[0])
    apply_props(head)
    for line in lines[1:]:
        paragraph = etree.SubElement(tx_body, A_P)
        apply_props(paragraph)
        fill(paragraph, line)


def _set_shape_text(element: Any, text: str) -> None:
    """文本形状（占位符 / 文本框 / 组内形状）的写回入口。"""
    tx_body = _first_of(element, _p("txBody"), A_TXBODY)
    if tx_body is None:
        raise ValueError(_SkipReason.NOT_TEXT)
    _write_tx_body(tx_body, text)


def _set_table_text(element: Any, text: str) -> None:
    """表格写回入口：`text` 以 `\\n` 分行、`\\t` 分格、格内 `\\v` 分段，与读出格式对称。

    只按模板现有行列取交集填充，不增删行列，避免撑破表格版式。
    """
    tbl = next(element.iter(A_TBL), None)
    if tbl is None:
        raise ValueError(_SkipReason.NOT_TEXT)
    rows = _kids(tbl, A_TR)
    for row_index, row_text in enumerate(text.split("\n")):
        tr = rows[row_index] if row_index < len(rows) else None
        if tr is None:
            continue
        cells = _kids(tr, A_TC)
        for cell_index, cell_text in enumerate(row_text.split("\t")):
            tc = cells[cell_index] if cell_index < len(cells) else None
            if tc is None:
                continue
            tx_body = _first(tc, A_TXBODY)
            if tx_body is not None:
                _write_tx_body(tx_body, cell_text.replace("\v", "\n"))


def _find_shape_element(doc: Any, shape_id: int) -> Any:
    """按 shapeId 定位形状元素，组内后代同样可达。"""
    target = next((x for x in doc.iter(P_CN_V_PR) if int(x.get("id") or 0) == shape_id), None)
    if target is None:
        return None
    element = target.getparent()
    while element is not None and element.tag not in SHAPE_ELEMENTS:
        element = element.getparent()
    return element


def apply_ops(
    data: bytes,
    text_ops: Sequence[SetTextOp] = (),
    delete_slides: Iterable[int] = (),
) -> tuple[bytes, ApplyResult]:
    """按指令改写 pptx，返回新文件字节与执行结果。

    待删页先收齐再处理文本：同一页上的文本改写必须让位于删页，
    否则指令顺序一变，写进被删页面的文字就成了静默丢弃。
    """
    inspected = _inspect(data)
    locator: dict[str, tuple[str, int, str]] = {}
    for slide in inspected:
        for shape in slide.shapes:
            locator[f"{slide.index}-{shape.shape_id}"] = (slide.path, shape.shape_id, shape.kind)

    result = ApplyResult()
    drop: set[int] = set()
    for index in delete_slides:
        if 0 <= index < len(inspected):
            drop.add(index)
        else:
            result.skipped.append(Skip(target=f"slide:{index}", reason="slide_not_found"))

    jobs_by_path: dict[str, list[_Job]] = {}
    for op in text_ops:
        where = locator.get(op.target)
        if where is None:
            result.skipped.append(Skip(target=op.target, reason=_SkipReason.SHAPE_NOT_FOUND))
            continue
        path, shape_id, kind = where
        if page_of_target(op.target) in drop:
            result.skipped.append(Skip(target=op.target, reason=_SkipReason.SLIDE_DELETED))
            continue
        if kind not in WRITABLE_KINDS:
            result.skipped.append(Skip(target=op.target, reason=_SkipReason.NOT_TEXT))
            continue
        jobs_by_path.setdefault(path, []).append(_Job(op.target, shape_id, kind, op.text))

    package = _Package(data)
    for path, jobs in jobs_by_path.items():
        doc = package.xml(path)
        if doc is None:
            for job in jobs:
                result.skipped.append(Skip(target=job.target, reason=_SkipReason.SLIDE_FILE_MISSING))
            continue
        for job in jobs:
            element = _find_shape_element(doc, job.shape_id)
            if element is None:
                result.skipped.append(Skip(target=job.target, reason=_SkipReason.SHAPE_NOT_FOUND))
                continue
            try:
                (_set_table_text if job.kind == "table" else _set_shape_text)(element, job.text)
            except ValueError as exc:
                result.skipped.append(Skip(target=job.target, reason=str(exc)))
                continue
            result.applied += 1
        package.set_xml(path, doc)

    result.deleted_slides = _drop_slides(package, inspected, drop)
    return package.write(), result


def strip_shell_watermarks(data: bytes, is_watermark: Callable[[str], bool]) -> tuple[bytes, int]:
    """剥掉版式与母版层里的来源水印。

    下载站的署名与网址常常只做在版式层，逐页改写看不见它，只能按 run 文本整段命中后清成空串。
    命中规则由调用方给定，版式里"单击此处添加标题"这类提示文字不会被误伤。
    """
    package = _Package(data)
    removed = 0
    for name in list(package.entries):
        if not _SHELL_PART.match(name):
            continue
        doc = package.xml(name)
        if doc is None:
            continue
        changed = False
        for node in doc.iter(A_T):
            text = node.text or ""
            if not text.strip() or not is_watermark(text):
                continue
            node.text = ""
            removed += 1
            changed = True
        if changed:
            package.set_xml(name, doc)
    if removed == 0:
        return data, 0
    return package.write(), removed


def _drop_dangling_links(package: _Package, inspected: Sequence[_Slide], parts: set[str]) -> None:
    """存活页里指向被删部件的关系必须删干净。

    目录页跳转到被删页的超链接：关系留着但目标部件已不存在，PowerPoint 会直接判文件损坏。
    引用该关系的 hlink 节点也要一并移除。
    """
    for slide in inspected:
        if slide.path in parts:
            continue
        rels = package.xml(slide.rels_path)
        if rels is None:
            continue
        dead: set[str] = set()
        rels_dirty = False
        for rel in list(rels.iter(RELATIONSHIP)):
            if part_path("ppt/slides/", rel.get("Target") or "") not in parts:
                continue
            dead.add(rel.get("Id"))
            rels.remove(rel)
            rels_dirty = True
        if not rels_dirty:
            continue
        package.set_xml(slide.rels_path, rels)

        doc = package.xml(slide.path)
        if doc is None:
            continue
        doc_dirty = False
        for tag in (A_HLINK_CLICK, A_HLINK_HOVER):
            for node in list(doc.iter(tag)):
                if node.get(_r("id")) not in dead:
                    continue
                node.getparent().remove(node)
                doc_dirty = True
        if doc_dirty:
            package.set_xml(slide.path, doc)


def _drop_slides(
    package: _Package,
    inspected: Sequence[_Slide],
    drop: set[int],
) -> list[int]:
    """删除整页。

    OOXML 里页面被四处引用，漏掉任一处 PowerPoint 都会判定文件损坏并弹修复提示：
    presentation.xml 的 sldIdLst（及分栏 sectionLst）、presentation.xml.rels、[Content_Types].xml，
    再连带删掉只被该页引用的备注页部件；其它页指向被删部件的超链接关系一并摘除。
    """
    if not drop:
        return []
    deleted = [slide for slide in inspected if slide.index in drop]
    if not deleted:
        return []

    slide_parts = {slide.path for slide in deleted}
    parts: set[str] = set()
    for slide in deleted:
        parts.add(slide.path)
        parts.add(slide.rels_path)

    # 备注页只服务对应 slide，删页后它就是无主部件
    for slide in deleted:
        rels = package.xml(slide.rels_path)
        if rels is None:
            continue
        for rel in rels.iter(RELATIONSHIP):
            if "notesSlide" not in (rel.get("Type") or ""):
                continue
            notes = part_path("ppt/slides/", rel.get("Target") or "")
            if not notes:
                continue
            parts.add(notes)
            parts.add(rels_path_for(notes))

    # presentation.xml.rels：记下被删页的关系 id 并移除关系
    removed_ids: set[str] = set()
    pres_rels = package.xml("ppt/_rels/presentation.xml.rels")
    if pres_rels is not None:
        dirty = False
        for rel in list(pres_rels.iter(RELATIONSHIP)):
            if part_path("ppt/", rel.get("Target") or "") not in slide_parts:
                continue
            removed_ids.add(rel.get("Id"))
            pres_rels.remove(rel)
            dirty = True
        if dirty:
            package.set_xml("ppt/_rels/presentation.xml.rels", pres_rels)

    pres = package.xml("ppt/presentation.xml")
    if pres is not None and removed_ids:
        removed_sld_ids: set[str] = set()
        sld_id_lst = _first(pres, _p("sldIdLst"))
        if sld_id_lst is not None:
            for node in list(_kids(sld_id_lst, _p("sldId"))):
                if node.get(_r("id")) not in removed_ids:
                    continue
                if sid := node.get("id"):
                    removed_sld_ids.add(sid)
                sld_id_lst.remove(node)

        # 分栏按 sldId 数值引用页面，页没了要让栏同步，否则残留悬空引用
        section_lst = _first(pres, _p("sectionLst"))
        if section_lst is not None and removed_sld_ids:
            for section in list(_kids(section_lst, _p("section"))):
                lst = _first(section, _p("sldIdLst"))
                if lst is None:
                    continue
                for ref in list(_kids(lst, _p("sldId"))) + list(_kids(lst, _p("sldIdRef"))):
                    if ref.get("id") in removed_sld_ids:
                        lst.remove(ref)
                if not _kids(lst, _p("sldId")) and not _kids(lst, _p("sldIdRef")):
                    section_lst.remove(section)
                    continue
                # PowerPoint 写的栏首项恒为 p:sldId（栏的锚点页），锚点被删就把首项提升回 p:sldId
                head = _first_element(lst)
                if head is not None and head.tag == _p("sldIdRef"):
                    head.addprevious(etree.Element(_p("sldId"), {"id": head.get("id") or ""}))
                    lst.remove(head)
            if not _kids(section_lst, _p("section")):
                section_lst.getparent().remove(section_lst)
        package.set_xml("ppt/presentation.xml", pres)

    content_types = package.xml("[Content_Types].xml")
    if content_types is not None:
        dirty = False
        for override in list(content_types.iter("Override")):
            name = (override.get("PartName") or "").lstrip("/")
            if name not in parts:
                continue
            content_types.remove(override)
            dirty = True
        if dirty:
            package.set_xml("[Content_Types].xml", content_types)

    _drop_dangling_links(package, inspected, parts)
    for part in parts:
        package.remove(part)
    return sorted(slide.index for slide in deleted)


__all__ = [
    "ApplyResult",
    "Skip",
    "apply_ops",
    "parse_structure",
    "strip_shell_watermarks",
]
