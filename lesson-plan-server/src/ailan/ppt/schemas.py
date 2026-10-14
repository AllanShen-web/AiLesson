"""课件接口的数据形状。

三份契约放在一起：
- `PresentationDoc` 是 AI 生成、在线编辑与导出 pptx 共用的唯一数据源（编辑器存什么，导出器读什么）；
- `TemplateStructure` 是导入外部模板后解析出的"外壳结构"，AI 改写页面时唯一的依据；
- 其余是增删改查与改写任务的出入参。

代码内部一律 snake_case，出接口才转 camelCase；`doc`/`outline`/`structure` 落库也是 snake_case，
所以这三份 JSON 一旦离开 Pydantic 就必须先 `model_dump(by_alias=False)`。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import Field

from ailan.core.schemas import ApiModel, ListQuery

# 学段：小学 / 中学 / 大学（含高职），驱动视觉风格与内容密度
Stage = Literal["primary", "middle", "college"]

# 装饰风格：band 色块 / card 卡片 / plain 简约
ThemeStyle = Literal["plain", "band", "card"]

# 单页版式：基础 6 种 + 富版式 5 种（大数字/图表/时间线/对比/课堂活动）
SlideLayout = Literal[
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
]

Status = Literal["outline", "done"]
Source = Literal["generated", "imported"]
# 预览的两个版本：模板外壳 / 改写产物
Revision = Literal["original", "working"]

TITLE_MAX = 200
LABEL_MAX = 20
ORIGINAL_NAME_MAX = 255
# 模板导入只收 .pptx：旧的 .ppt 是二进制格式，解析不了；上限与资料库一致
TEMPLATE_SIZE_LIMIT = 50 * 1024 * 1024


class BigNumber(ApiModel):
    """大数字页：一个核心数字 + 说明，用于概念导入和关键结论。"""

    value: str
    unit: str | None = None
    label: str | None = None


class ChartSeries(ApiModel):
    name: str
    data: list[float] = Field(default_factory=list)


class Chart(ApiModel):
    """图表数据，用 pptx 原生图表对象画，不需要先转成图片。"""

    type: Literal["bar", "line", "pie"]
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)


class TimelineStep(ApiModel):
    title: str
    desc: str | None = None


class CompareSide(ApiModel):
    title: str
    bullets: list[str] = Field(default_factory=list)


class Compare(ApiModel):
    left: CompareSide
    right: CompareSide


class Activity(ApiModel):
    """课堂活动页：活动名 + 操作步骤。"""

    name: str
    steps: list[str] = Field(default_factory=list)


class Slide(ApiModel):
    layout: SlideLayout = "title-bullets"
    title: str = ""
    # 要点列表；two-column 版式下前半为左栏、后半为右栏
    bullets: list[str] = Field(default_factory=list)
    # 正文兜底（bullets 为空时使用）
    content: str | None = None
    notes: str | None = None
    big_number: BigNumber | None = None
    chart: Chart | None = None
    timeline: list[TimelineStep] | None = None
    compare: Compare | None = None
    activity: Activity | None = None


class Theme(ApiModel):
    # 主题色（十六进制，不带 #）
    primary: str = ""
    bg: str = ""
    title_color: str = ""
    text_color: str = ""
    font: str = ""
    # 辅助色（装饰、渐变、编号底），缺省时由 primary 提亮派生
    accent: str | None = None
    # 装饰风格：band 色块 / card 卡片 / plain 简约
    style: ThemeStyle = "band"


class PresentationDoc(ApiModel):
    title: str = ""
    theme: Theme = Field(default_factory=Theme)
    slides: list[Slide] = Field(default_factory=list)
    # 学段决定视觉风格与内容密度，缺省按中学
    stage: Stage = "middle"


class OutlineSection(ApiModel):
    """大纲条目：先列大纲阶段的产物，生成页面内容时按它逐页展开。"""

    title: str
    points: list[str] = Field(default_factory=list)
    # 大纲阶段就把版式分布规划好，避免整份课件每页雷同
    layout: SlideLayout | None = None


class Outline(ApiModel):
    sections: list[OutlineSection] = Field(default_factory=list)


class TemplateShape(ApiModel):
    """模板外壳里的一个形状。

    `target` 形如 `3-15`（解析时的 0 基页序 + 形状 id），是改写指令定位形状的唯一句柄：
    形状 id 在同一份文件里稳定，页序一旦删页就会变，所以回写之前不能改写它。
    """

    target: str
    name: str
    shape_id: int
    # placeholder / textbox / table / picture / chart / group / shape
    kind: str
    placeholder_type: str | None = None
    placeholder_index: int | None = None
    text: str = ""
    # 位置与尺寸，单位英寸；组内子形状不叠加组变换，只作存档
    left: float | None = None
    top: float | None = None
    width: float | None = None
    height: float | None = None


class TemplateSlide(ApiModel):
    index: int
    notes: str = ""
    shapes: list[TemplateShape] = Field(default_factory=list)


class TemplateStructure(ApiModel):
    """`Presentation.structure` 的形状：逐页形状清单 + 备注。"""

    slide_count: int
    slides: list[TemplateSlide] = Field(default_factory=list)


class SetTextOp(ApiModel):
    """一条文本替换指令：整框换成 `text`。

    表格槽的 `text` 用 `\n` 分行、`\t` 分列、`\v` 分格内段落，与解析时的读出格式对称。
    """

    op: Literal["set_text"] = "set_text"
    target: str
    text: str = ""


class PresentationView(ApiModel):
    """对外视图：pptx 字节与对象 key 一律不带——桶是匿名可读的，key 泄漏等于文件泄漏。"""

    id: int
    title: str
    subject: str | None = None
    grade: str | None = None
    lesson_plan_id: int | None = None
    status: Status
    outline: Outline | dict | None = None
    doc: PresentationDoc
    source: Source
    original_name: str | None = None
    # 导入记录才有：还没改写时是 False，不算数据缺失
    has_working: bool = False
    structure: TemplateStructure | None = None
    is_public: bool
    user_id: int
    creator_name: str
    created_at: datetime
    updated_at: datetime


class PresentationCreate(ApiModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    subject: str | None = Field(default=None, max_length=LABEL_MAX)
    grade: str | None = Field(default=None, max_length=LABEL_MAX)
    lesson_plan_id: int | None = None
    # 编辑器保存的完整文档；AI 生成流程由 Agent 工具内部写入，不经过这里
    doc: PresentationDoc | None = None
    is_public: bool = False


class PresentationUpdate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    subject: str | None = Field(default=None, max_length=LABEL_MAX)
    grade: str | None = Field(default=None, max_length=LABEL_MAX)
    doc: PresentationDoc | None = None
    is_public: bool | None = None


class PresentationQuery(ListQuery):
    """课件列表的过滤条件；`sort_field` 默认按最后更新时间，编辑器里最近改的排最前。"""

    subject: str | None = Field(default=None, description="学科，模糊匹配")
    grade: str | None = Field(default=None, description="年级，精确匹配")
    sort_field: str | None = Field(default="updatedAt", description="排序字段：createdAt / updatedAt")


class RewriteInput(ApiModel):
    lesson_plan_id: int = Field(gt=0, description="用哪篇教案改写这套模板")


class SlotIssue(ApiModel):
    """改写报告里的一条留痕：哪个框、原来写着什么。"""

    target: str
    text: str


class RewriteReport(ApiModel):
    """改写报告：前端据此跳转新记录，并如实展示没处理到的地方。"""

    id: int
    title: str
    pages: int
    template_pages: int
    applied: int
    deleted_slides: list[int] = Field(default_factory=list)
    watermark_slots: int
    echoed_fixed: int
    cleared_slots: int
    page_plan_failed_batches: int
    not_editable_texts: int
    unrewritten: list[SlotIssue] = Field(default_factory=list)
    blanked: list[SlotIssue] = Field(default_factory=list)


class RewriteTaskView(ApiModel):
    """后台改写任务的对外视图：只在进程内存活，前端轮询它显示进度。

    这里叫 `ppt_id` 而不是 `presentation_id`：SSE 事件和请求体里的课件编号一律是 `pptId`，
    前端把轮询结果和事件流合到同一张进度表里，字段名必须一致。
    """

    id: str
    ppt_id: int
    lesson_plan_id: int
    status: Literal["queued", "running", "done", "error"]
    stage: str
    done: int
    total: int
    created_at: datetime
    report: RewriteReport | None = None
    error: str | None = None


PresentationQueryDep = Annotated[PresentationQuery, Depends(PresentationQuery)]
