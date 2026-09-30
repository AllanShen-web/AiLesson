"""教案接口的数据形状。

教案 = 模板结构的一份填写实例。`content` 里同时存当时的字段快照和填进去的值，
所以模板改了、删了，已写好的教案仍然是那个样子，导出也照旧排版。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends
from pydantic import Field

from ailan.core.schemas import ApiModel, ListQuery
from ailan.templates.schemas import FormField

TITLE_MAX = 200
BASIC_LABEL_MAX = 20
TOPIC_MAX = 60
OPTION_COUNT_MAX = 80

# 新建教案时要回填的三项基本信息；候选列表由前端按模板字段与年级树传进来
BASIC_FIELD_KEYS: tuple[str, ...] = ("subject", "grade", "unit")


class LessonPlanContent(ApiModel):
    fields: list[FormField] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)


class LessonPlanView(ApiModel):
    id: int
    title: str
    subject: str | None = None
    grade: str | None = None
    template_id: int | None = None
    content: LessonPlanContent
    is_public: bool
    user_id: int
    creator_name: str
    created_at: datetime
    updated_at: datetime


class LessonPlanCreate(ApiModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    subject: str | None = Field(default=None, max_length=BASIC_LABEL_MAX)
    grade: str | None = Field(default=None, max_length=BASIC_LABEL_MAX)
    template_id: int | None = None
    content: LessonPlanContent | None = None
    is_public: bool = False


class LessonPlanUpdate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    subject: str | None = Field(default=None, max_length=BASIC_LABEL_MAX)
    grade: str | None = Field(default=None, max_length=BASIC_LABEL_MAX)
    content: LessonPlanContent | None = None
    is_public: bool | None = None


class LessonPlanQuery(ListQuery):
    """列表页的过滤条件：学科、年级、关键字，加上分页与排序。

    `is_public` 不是"取公共库"的开关——公共库有自己的路由，这里筛的是"只看公开 / 只看未公开"，
    名字与返回体里的 `isPublic` 对齐，请求和响应同一份拼写。
    """

    subject: str | None = Field(default=None, description="学科，模糊匹配")
    grade: str | None = Field(default=None, description="年级，前缀匹配（历史数据带学期后缀）")
    is_public: bool | None = Field(default=None, description="只看已公开 / 未公开")
    sort_field: str | None = Field(default=None, description="排序字段：createdAt / updatedAt")


class BasicPredictionInput(ApiModel):
    topic: str = Field(min_length=1, max_length=TOPIC_MAX)
    # {"subject": [...], "grade": [...], "unit": [...]}：给了候选就必须逐字命中，否则该字段留空
    options: dict[str, list[str]] = Field(default_factory=dict)


class BasicPrediction(ApiModel):
    subject: str = ""
    grade: str = ""
    unit: str = ""


LessonPlanQueryDep = Annotated[LessonPlanQuery, Depends(LessonPlanQuery)]
