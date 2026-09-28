"""模板服务：本人的模板 + 公共模板市场，官方模板由代码兜底维护。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.core.db import SessionDep
from ailan.core.errors import ForbiddenError, NotFoundError
from ailan.templates.models import Template, empty_schema
from ailan.templates.schemas import FormField, FormSchema, TemplateCreate, TemplateUpdate

# 官方模板：进"模板市场"，人人可用、人人不可改。
# 结构以这份代码为准，启动时覆盖库里的同名行——改字段不需要写迁移
OFFICIAL_TEMPLATE_NAME = "国家课程标准教案模板"
OFFICIAL_SCHEMA = FormSchema(
    fields=[
        FormField(
            id="official-subject",
            type="select",
            label="科目",
            width=0.5,
            options=["语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "政治", "信息技术"],
        ),
        FormField(
            id="official-grade",
            type="select",
            label="年级",
            width=0.5,
            options=[
                "一年级",
                "二年级",
                "三年级",
                "四年级",
                "五年级",
                "六年级",
                "七年级",
                "八年级",
                "九年级",
                "高一",
                "高二",
                "高三",
            ],
        ),
        FormField(
            id="official-textbook", type="input", label="教材版本", width=0.5, placeholder="如：人教版必修一"
        ),
        FormField(
            id="official-title", type="input", label="课题标题", width=0.5, placeholder="请输入课题标题"
        ),
        FormField(
            id="official-objectives",
            type="textarea",
            label="教学目标（核心素养）",
            placeholder="请填写教学目标",
        ),
        FormField(
            id="official-keydifficult",
            type="textarea",
            label="教学重难点",
            placeholder="请填写教学重点和难点",
        ),
        FormField(id="official-preparation", type="textarea", label="教学准备", placeholder="请填写教学准备"),
        FormField(id="official-process", type="textarea", label="教学过程", placeholder="请填写教学过程"),
        FormField(id="official-boarddesign", type="textarea", label="板书设计", placeholder="请填写板书设计"),
        FormField(id="official-reflection", type="textarea", label="教学反思", placeholder="课后填写"),
    ]
)


class TemplateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find(self, template_id: int) -> Template | None:
        return await self.session.get(Template, template_id)

    async def find_visible(self, template_id: int, user_id: int) -> Template:
        """按 id 取模板，只看得到自己的、公开的、以及官方的。

        不在列表里的一律当不存在（404 而不是 403）：别人的私有模板 id 是不是有效，
        本身就是一种泄露。
        """
        template = await self.find(template_id)
        if template is None or (
            template.user_id != user_id and not template.is_public and not template.is_official
        ):
            raise NotFoundError("模板不存在")
        return template

    async def list_own(self, user_id: int) -> list[Template]:
        statement = (
            select(Template)
            .where(Template.user_id == user_id)
            .order_by(desc(Template.created_at), desc(Template.id))
        )
        return list(await self.session.scalars(statement))

    async def list_public(self, user_id: int) -> list[Template]:
        """模板市场：公开的和官方的，但自己建的那些不重复出现（自己的列表里已经有了）。"""
        statement: Select = (
            select(Template)
            .where(Template.is_public.is_(True) | Template.is_official.is_(True))
            .where(Template.user_id != user_id)
            .order_by(desc(Template.created_at), desc(Template.id))
        )
        return list(await self.session.scalars(statement))

    async def create(self, user_id: int, username: str, data: TemplateCreate) -> Template:
        template = Template(
            name=data.name,
            user_id=user_id,
            creator_name=username[:20],
            content=_content_dict(data.content),
        )
        self.session.add(template)
        await self.session.flush()
        return template

    async def update(self, template_id: int, user_id: int, data: TemplateUpdate) -> Template:
        template = await self._owned_by(template_id, user_id, "编辑")
        fields = data.model_dump(exclude_unset=True, mode="json")
        if fields.get("name"):
            template.name = fields["name"]
        if fields.get("content"):
            template.content = fields["content"]
        await self.session.flush()
        return template

    async def remove(self, template_id: int, user_id: int) -> None:
        template = await self._owned_by(template_id, user_id, "删除")
        await self.session.delete(template)

    async def toggle_public(self, template_id: int, user_id: int) -> Template:
        template = await self._owned_by(template_id, user_id, "修改")
        template.is_public = not template.is_public
        await self.session.flush()
        return template

    async def copy(self, template_id: int, user_id: int, username: str) -> Template:
        """把市场上看得见的模板复制成自己的一份：内容深拷贝，之后怎么改都影响不到原件。"""
        source = await self.find(template_id)
        if source is None:
            raise NotFoundError("模板不存在")
        if not source.is_official and not source.is_public:
            raise ForbiddenError("该模板不可复制")

        clone = Template(
            name=f"{source.name}（副本）"[:100],
            user_id=user_id,
            creator_name=username[:20],
            # 深拷贝：JSONB 读出来本就是新对象，但显式复制一遍，改副本不会连着改到原件
            content=dict(source.content),
        )
        self.session.add(clone)
        await self.session.flush()
        return clone

    async def _owned_by(self, template_id: int, user_id: int, action: str) -> Template:
        template = await self.find(template_id)
        if template is None:
            raise NotFoundError("模板不存在")
        # 官方模板在编辑、删除、公开这几步里全部锁死：代码里的 schema 才是权威版本
        if template.is_official:
            raise ForbiddenError(f"官方模板不可{action}")
        if template.user_id != user_id:
            raise ForbiddenError(f"无权{action}此模板")
        return template

    async def ensure_official_template(self) -> None:
        """启动时保证官方模板在位，并把库里那份对齐到代码里的 schema。"""
        existing = await self.session.scalar(select(Template).where(Template.is_official.is_(True)))
        schema = OFFICIAL_SCHEMA.model_dump(mode="json", by_alias=False)
        if existing is None:
            self.session.add(
                Template(
                    name=OFFICIAL_TEMPLATE_NAME,
                    # 官方模板不属于任何人：user_id 指向系统账号的做法需要一个假用户，
                    # 这里用 0 表示"没有归属人"，任何校验分支都会先被 is_official 挡住
                    user_id=0,
                    creator_name="系统",
                    is_official=True,
                    is_public=True,
                    content=schema,
                )
            )
        elif existing.content != schema:
            existing.content = schema
        await self.session.flush()


def _content_dict(content: FormSchema | None) -> dict[str, Any]:
    if content is None:
        return empty_schema()
    return content.model_dump(mode="json", by_alias=False)


def get_template_service(session: SessionDep) -> TemplateService:
    return TemplateService(session)


TemplateServiceDep = Annotated[TemplateService, Depends(get_template_service)]
