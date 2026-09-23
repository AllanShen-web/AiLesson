"""列表接口的公共数据形状：分页参数与分页结果。

排序字段走白名单而不是直接拼进 SQL：请求里带什么字段名都可能，
`getattr(model, sort_field)` 一旦不加约束，等于把列名和查询结构交给调用方。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import Select, asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200


class ApiModel(BaseModel):
    """所有入参与出参的基类。

    代码内部一律 snake_case，出去是浏览器习惯的 camelCase：只在边界上转换一次，
    不把 `roleCode` 这种拼写渗进 Python 代码里。
    `extra="ignore"` 让多余的字段被丢掉而不是原样落库——前端表单常带上一版接口的残留字段。
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        str_strip_whitespace=True,
        extra="ignore",
    )


class ListQuery(ApiModel):
    """列表接口的公共查询参数，用 `Depends(ListQuery)` 直接展开成 query。

    继承 `ApiModel` 是让 query 的名字和返回体同源：代码里读 `page_size`，
    请求里写 `pageSize`，浏览器不必在同一套接口里混两种拼写。
    """

    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(
        default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description=f"每页条数，上限 {MAX_PAGE_SIZE}"
    )
    keyword: str | None = Field(default=None, description="模糊匹配，具体匹配哪些列由接口决定")
    sort_field: str | None = Field(default=None, description="排序字段，须在接口给出的白名单内")
    sort_order: Literal["asc", "desc"] | None = Field(default=None, description="升序或降序，大小写不敏感")

    @field_validator("sort_order", mode="before")
    @classmethod
    def _normalize_order(cls, value: object) -> object:
        """`ASC` / `DESC` 也接得住：比较只认小写这一份口径。"""
        return value.lower() if isinstance(value, str) else value

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size

    def order_by(self, allowed: dict[str, ColumnElement[object]]) -> list[ColumnElement[object]]:
        """把请求里的字段名换成真正允许排序的列；不在白名单里就退回默认排序。

        白名单的键写的就是请求里的样子（`createdAt` 而不是 `created_at`）：
        排序参数和返回体里的那些列名是同一份拼写，调用方不用猜第二套。
        """
        if not self.sort_field:
            return []
        column = allowed.get(self.sort_field)
        if column is None:
            return []
        return [asc(column) if self.sort_order == "asc" else desc(column)]


async def count_rows(session: AsyncSession, statement: Select) -> int:
    """按同一套过滤条件取总数，省掉手写两遍 where。

    先 `order_by(None)` 清掉排序：子查询里带 ORDER BY 在 Postgres 合法但白排一遍，
    而且某些驱动会直接报错。
    """
    total = await session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    return int(total or 0)


class Page[T](ApiModel):
    """统一的分页返回：列表在 `items`，总数在 `total`，`page`/`pageSize` 回显这一次请求用的分页参数。"""

    items: list[T] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


ListQueryDep = Annotated[ListQuery, Depends(ListQuery)]
