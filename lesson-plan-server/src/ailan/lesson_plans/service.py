"""教案服务：列表过滤、公共库、复制与基本信息推断。"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import Select, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Text

from ailan.ai_chat.config_service import AssistantConfigService, AssistantConfigServiceDep
from ailan.core.db import SessionDep
from ailan.core.errors import BadRequestError, ForbiddenError, NotFoundError
from ailan.core.schemas import Page, count_rows
from ailan.lesson_plans.models import LessonPlan, empty_content
from ailan.lesson_plans.schemas import (
    OPTION_COUNT_MAX,
    TOPIC_MAX,
    BasicPrediction,
    BasicPredictionInput,
    LessonPlanContent,
    LessonPlanCreate,
    LessonPlanQuery,
    LessonPlanUpdate,
    LessonPlanView,
)
from ailan.llm.factory import build_model
from ailan.llm.text import empty_content_hint, model_text, pick_basic_field, pick_json_object

logger = logging.getLogger(__name__)

TITLE_SUFFIX = "（副本）"
# 公共库的"不分页"取全量给侧边搜索用，但必须有上限：这一条 SQL 一旦不加 LIMIT，
# 库大了就是几十 MB 的 JSON 往浏览器灌
ALL_ROWS_LIMIT = 5000

SORTABLE: dict[str, Any] = {
    "createdAt": LessonPlan.created_at,
    "updatedAt": LessonPlan.updated_at,
}

PREDICT_BASIC_SYSTEM = "\n".join(
    [
        "你在为教师新建教案，只给课题名称，要推断三项基本信息：学科、年级、单元。规则：",
        "- 字段给了候选列表时，答案必须逐字来自候选；不在候选内或不确定就返回空字符串。",
        '- 单元按该课题在现行主流教材中的实际归属给出（如"第三单元"或教材中的单元标题），'
        "无法确定就返回空字符串。",
        "- 禁止编造。宁可返回空，也不要给看似合理但没有依据的答案。",
        '只返回 JSON，不要任何解释：{"subject":"","grade":"","unit":""}',
    ]
)


class LessonPlanService:
    def __init__(self, session: AsyncSession, configs: AssistantConfigService) -> None:
        self.session = session
        self.configs = configs

    async def list_by_user(self, user_id: int, query: LessonPlanQuery) -> Page[LessonPlanView]:
        statement = self._filtered(select(LessonPlan).where(LessonPlan.user_id == user_id), query)
        return await self._page(statement, query)

    async def list_public(self, user_id: int, query: LessonPlanQuery) -> Page[LessonPlanView]:
        """公共库：公开的、但不是自己的（自己的那份在"我的教案"里已经有了）。"""
        statement = self._filtered(
            select(LessonPlan).where(LessonPlan.is_public.is_(True), LessonPlan.user_id != user_id), query
        )
        return await self._page(statement, query)

    async def list_own_all(self, user_id: int, query: LessonPlanQuery) -> list[LessonPlanView]:
        """不分页取自己的教案：「按课题找同名」那类交互要的是全量，不是第 1 页。"""
        statement = self._filtered(select(LessonPlan).where(LessonPlan.user_id == user_id), query)
        rows = await self.session.scalars(self._ordered(statement, query).limit(ALL_ROWS_LIMIT))
        return [_view(plan) for plan in rows]

    async def list_public_all(self, user_id: int, query: LessonPlanQuery) -> list[LessonPlanView]:
        statement = self._filtered(
            select(LessonPlan).where(LessonPlan.is_public.is_(True), LessonPlan.user_id != user_id), query
        )
        rows = await self.session.scalars(self._ordered(statement, query).limit(ALL_ROWS_LIMIT))
        return [_view(plan) for plan in rows]

    async def find_visible(self, plan_id: int, user_id: int) -> LessonPlan:
        plan = await self.session.get(LessonPlan, plan_id)
        if plan is None:
            raise NotFoundError("教案不存在")
        if not plan.is_public and plan.user_id != user_id:
            raise ForbiddenError("无权查看此教案")
        return plan

    async def create(self, user_id: int, username: str, data: LessonPlanCreate) -> LessonPlan:
        plan = LessonPlan(
            title=data.title,
            subject=data.subject,
            grade=data.grade,
            template_id=data.template_id,
            content=_content_dict(data.content),
            is_public=data.is_public,
            user_id=user_id,
            creator_name=username[:20],
        )
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def update(self, plan_id: int, user_id: int, data: LessonPlanUpdate) -> LessonPlan:
        plan = await self._owned_by(plan_id, user_id, "编辑")
        fields = data.model_dump(exclude_unset=True, mode="json")
        for key in ("title", "subject", "grade", "is_public"):
            if fields.get(key) is not None:
                setattr(plan, key, fields[key])
        if fields.get("content"):
            plan.content = fields["content"]
        await self.session.flush()
        return plan

    async def remove(self, plan_id: int, user_id: int) -> None:
        plan = await self._owned_by(plan_id, user_id, "删除")
        await self.session.delete(plan)

    async def toggle_public(self, plan_id: int, user_id: int) -> LessonPlan:
        plan = await self._owned_by(plan_id, user_id, "修改")
        plan.is_public = not plan.is_public
        await self.session.flush()
        return plan

    async def copy(self, plan_id: int, user_id: int, username: str) -> LessonPlan:
        source = await self.find_visible(plan_id, user_id)
        clone = LessonPlan(
            title=f"{source.title}{TITLE_SUFFIX}"[:200],
            subject=source.subject,
            grade=source.grade,
            template_id=source.template_id,
            # 复制一份内容而不是共享引用：改副本不能连着改到原件
            content=dict(source.content),
            is_public=False,
            user_id=user_id,
            creator_name=username[:20],
        )
        self.session.add(clone)
        await self.session.flush()
        return clone

    async def find_same_topic(self, user_id: int, topic: str) -> LessonPlan | None:
        """按课题名找同名教案：新建时先查这一步，命中就不用麻烦模型推断。"""
        title = topic.strip()
        if not title:
            return None
        return await self.session.scalar(
            select(LessonPlan)
            .where(LessonPlan.user_id == user_id, func.lower(LessonPlan.title) == func.lower(title))
            .order_by(desc(LessonPlan.updated_at))
            .limit(1)
        )

    async def predict_basic(self, payload: BasicPredictionInput) -> BasicPrediction:
        """按课题名推断学科 / 年级 / 单元，供新建教案自动回填兜底。

        这是增强项：两次都不成就三个字段全空返回，不报错——但要在服务端留一行原因，
        否则调用方只能看到"没反应"。
        """
        topic = payload.topic.strip()[:TOPIC_MAX]
        if not topic:
            raise BadRequestError("请先填写课题名称")

        options = {key: _clean_options(payload.options.get(key)) for key in ("subject", "grade", "unit")}
        spec = await self.configs.spec_or_throw()
        # 不覆盖 max_tokens：思考型模型会先把输出预算烧在推理上，预算太小就只剩空正文
        model = build_model(spec, streaming=False, thinking=False)
        message = _prediction_message(topic, options)
        reason = "未发起调用"

        for attempt in range(1, 3):
            try:
                response = await model.ainvoke(
                    [SystemMessage(content=PREDICT_BASIC_SYSTEM), HumanMessage(content=message)]
                )
                text = model_text(response).strip()
                metadata = getattr(response, "response_metadata", None) or {}
                reason = (
                    f"第{attempt}次 finish={metadata.get('finish_reason', '?')} "
                    f"正文={text[:200] or empty_content_hint(response)}"
                )

                parsed = pick_json_object(text)
                if parsed is None:
                    continue
                prediction = BasicPrediction(
                    **{key: pick_basic_field(parsed.get(key), options[key]) for key in options}
                )
                if prediction.subject or prediction.grade or prediction.unit:
                    return prediction
                reason = f"{reason} → 三个字段要么为空要么没逐字命中候选"
            except Exception as exc:
                reason = f"第{attempt}次调用异常 {exc}"

        logger.warning("[predict-basic] 未产出可用推断，topic=%s；%s", topic, reason)
        return BasicPrediction()

    async def _owned_by(self, plan_id: int, user_id: int, action: str) -> LessonPlan:
        plan = await self.session.get(LessonPlan, plan_id)
        if plan is None:
            raise NotFoundError("教案不存在")
        if plan.user_id != user_id:
            raise ForbiddenError(f"无权{action}此教案")
        return plan

    def _filtered(self, statement: Select, query: LessonPlanQuery) -> Select:
        if query.subject:
            statement = statement.where(LessonPlan.subject.ilike(f"%{query.subject}%"))
        if query.grade:
            # 前缀匹配：历史教案的年级仍带「上学期/下学期」后缀，选择器粒度已收到年级
            statement = statement.where(LessonPlan.grade.ilike(f"{query.grade}%"))
        if query.is_public is not None:
            statement = statement.where(LessonPlan.is_public.is_(query.is_public))
        if query.keyword:
            pattern = f"%{query.keyword}%"
            # 内容整段进 JSON 的文本形式里搜：栏目标题改了、id 变了，正文里的字还搜得到
            statement = statement.where(
                or_(LessonPlan.title.ilike(pattern), cast(LessonPlan.content, Text).ilike(pattern))
            )
        return statement

    def _ordered(self, statement: Select, query: LessonPlanQuery) -> Select:
        # 白名单在 SORTABLE 里：请求带来的字段名换不成任意列，退默认排序而不是拼进 SQL
        order = query.order_by(SORTABLE)
        if order:
            return statement.order_by(*order)
        return statement.order_by(desc(LessonPlan.created_at))

    async def _page(self, statement: Select, query: LessonPlanQuery) -> Page[LessonPlanView]:
        ordered = self._ordered(statement, query)
        total = await count_rows(self.session, statement)
        rows = await self.session.scalars(ordered.offset(query.offset).limit(query.limit))
        return Page[LessonPlanView](
            items=[_view(plan) for plan in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )


def _content_dict(content: LessonPlanContent | None) -> dict[str, Any]:
    if content is None:
        return empty_content()
    return content.model_dump(mode="json", by_alias=False)


def _clean_options(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()][:OPTION_COUNT_MAX]


def _prediction_message(topic: str, options: dict[str, list[str]]) -> str:
    lines = [f"课题：{topic}"]
    for key, label in (("subject", "学科"), ("grade", "年级"), ("unit", "单元")):
        if options[key]:
            lines.append(f"{label}候选（逐字选一）：{'、'.join(options[key])}")
    return "\n".join(lines)


def _view(plan: LessonPlan) -> LessonPlanView:
    return LessonPlanView.model_validate(plan)


def get_lesson_plan_service(session: SessionDep, configs: AssistantConfigServiceDep) -> LessonPlanService:
    return LessonPlanService(session, configs)


LessonPlanServiceDep = Annotated[LessonPlanService, Depends(get_lesson_plan_service)]
