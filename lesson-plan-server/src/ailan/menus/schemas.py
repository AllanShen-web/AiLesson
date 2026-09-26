"""菜单接口的数据形状。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ailan.core.schemas import ApiModel
from ailan.menus.models import Menu

PATH_MAX = 200


class MenuView(ApiModel):
    id: int
    name: str
    icon: str
    path: str
    sort: int
    parent_id: int | None = None
    created_at: datetime

    @classmethod
    def of(cls, menu: Menu) -> MenuView:
        return cls.model_validate(menu)


class MenuCreate(ApiModel):
    name: str = Field(min_length=1, max_length=50)
    # 图标名由前端的一组图标集提供，后端只存字符串，不认识也照样存：加图标不该要求改后端
    icon: str = Field(min_length=1, max_length=50)
    # path 既是侧边栏跳转目标，也是角色授权用的键，所以这里限制得比展示字段严
    path: str = Field(min_length=1, max_length=PATH_MAX)
    sort: int = Field(default=0, ge=0)
    parent_id: int | None = None


class MenuUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    icon: str | None = Field(default=None, min_length=1, max_length=50)
    path: str | None = Field(default=None, min_length=1, max_length=PATH_MAX)
    sort: int | None = Field(default=None, ge=0)
    # 显式传 null 表示"提到顶层"，所以这里不能像别处那样把 None 当"不改"
    parent_id: int | None = None
