"""模板接口的数据形状：模板 = 一份可配置的表单结构。

`FormField` 是这套结构里唯一的复杂度来源：宽度只有十二档、标签只有三种位置、
`label` / `divider` 两种"没有内容"的类型。导出成 Word 时全部靠这几个字段排版，
所以这里的约束必须写死在边界上，而不是等渲染时再兜。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ailan.core.schemas import ApiModel

FieldType = Literal["input", "textarea", "select", "number", "label", "divider"]
LabelPosition = Literal["left", "top", "none"]

LABEL_MAX = 30
OPTION_MAX = 30
OPTION_COUNT_MAX = 20
NAME_MAX = 100
CREATOR_NAME_MAX = 20

# 宽度按 1/12 递增共十二档：编辑器里的宽度选择器只有这十二个值，
# 落库前把任意数字吸附到最近的档位，导出的表格才对得齐
WIDTH_STEPS: tuple[float, ...] = tuple(index / 12 for index in range(1, 13))

ROWS_MIN, ROWS_MAX = 2, 20
ROW_SPAN_MIN, ROW_SPAN_MAX = 1, 12

# 没写 width 时按半宽兜底的历史字段 id：官方模板和早期模板的这几项就是 2×2 排版，
# 前端展示用的也是同一份口径，改这里要一起改
HALF_WIDTH_DEFAULT_IDS = frozenset(
    {
        "basic-topic",
        "basic-unit",
        "basic-subject",
        "basic-grade",
        "official-subject",
        "official-grade",
        "official-textbook",
        "official-title",
    }
)


def normalize_width(width: object, fallback: float) -> float:
    """把宽度吸附到最近的档位；缺失或非法值按默认宽度处理。"""
    if not isinstance(width, (int, float)) or width <= 0:
        return fallback
    return min(WIDTH_STEPS, key=lambda step: abs(step - width))


def default_width(field_id: str) -> float:
    return 0.5 if field_id in HALF_WIDTH_DEFAULT_IDS else 1.0


class FormField(ApiModel):
    """一个栏目。

    `label` = 纯栏目标题：只有标题、没有输入控件，用于模板里的标题套标题；
    `divider` = 分隔符：没有内容的整行断点，把它两侧的字段切成两张独立的表，连续两个再换页。
    """

    id: str = Field(min_length=1, max_length=60)
    type: FieldType
    label: str = Field(min_length=1, max_length=LABEL_MAX)
    placeholder: str | None = Field(default=None, max_length=LABEL_MAX)
    options: list[str] | None = Field(default=None, max_length=OPTION_COUNT_MAX)
    # 1/12 递增十二档：1/12 ~ 1
    width: float | None = Field(default=None, ge=1 / 12 - 1e-9, le=1)
    # 缺省 left：label 与内容左右并排；none 只留内容、不显示标签
    label_position: LabelPosition | None = None
    # 高度（行）：多行文本与纯栏目标题共用，缺省 4
    rows: int | None = Field(default=None, ge=ROWS_MIN, le=ROWS_MAX)
    # 纵向贯穿行数（仅纯栏目标题）：Word 里的竖排合并大标题，缺省 1
    row_span: int | None = Field(default=None, ge=ROW_SPAN_MIN, le=ROW_SPAN_MAX)

    def normalized(self) -> FormField:
        """补齐缺省并吸附宽度，交给导出的渲染层一个不用再做判断的形状。"""
        width = normalize_width(self.width, default_width(self.id))
        rows = self.rows
        if rows is not None and not ROWS_MIN <= rows <= ROWS_MAX:
            rows = None
        row_span = self.row_span
        if self.type != "label" or (row_span is not None and not ROW_SPAN_MIN <= row_span <= ROW_SPAN_MAX):
            row_span = None
        return self.model_copy(
            update={
                "width": width,
                "rows": rows,
                "row_span": row_span,
                "label_position": self.label_position or "left",
            }
        )


class FormSchema(ApiModel):
    fields: list[FormField] = Field(default_factory=list)


class TemplateView(ApiModel):
    id: int
    name: str
    user_id: int
    creator_name: str
    is_official: bool
    is_public: bool
    content: FormSchema
    created_at: datetime


class TemplateCreate(ApiModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    content: FormSchema | None = None


class TemplateUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    content: FormSchema | None = None


class TemplateDraft(ApiModel):
    """Word 导入返回的草稿：不落库，前端拿去填编辑器，教师确认后才走新建接口。"""

    name: str = Field(max_length=NAME_MAX)
    fields: list[FormField] = Field(default_factory=list, max_length=40)
