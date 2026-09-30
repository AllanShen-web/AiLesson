"""教案路由：我的教案、公共库与基本信息推断。

归属判断全在 service：详情和复制要放行"别人的公开教案"，保存和删除只放行本人，
两种口径用同一个接口层守卫表达不出来，交给各自方法里那几行明确的比较。
"""

from __future__ import annotations

from fastapi import APIRouter

from ailan.core.deps import CurrentUserDep
from ailan.core.envelope import EnvelopeRoute
from ailan.core.schemas import Page
from ailan.lesson_plans.schemas import (
    BasicPrediction,
    BasicPredictionInput,
    LessonPlanCreate,
    LessonPlanQueryDep,
    LessonPlanUpdate,
    LessonPlanView,
)
from ailan.lesson_plans.service import LessonPlanServiceDep

# 顺序要紧：`/all`、`/public`、`/predict-basic` 都要排在 `/{lesson_plan_id}` 之前，
# 否则这些字面量会被当成 id 去转 int
router = APIRouter(prefix="/lesson-plans", tags=["教案"], route_class=EnvelopeRoute)


@router.get("", response_model=Page[LessonPlanView], summary="我的教案")
async def list_my(
    user: CurrentUserDep, plans: LessonPlanServiceDep, query: LessonPlanQueryDep
) -> Page[LessonPlanView]:
    return await plans.list_by_user(user.id, query)


@router.get("/all", response_model=list[LessonPlanView], summary="我的教案（不分页）")
async def list_my_all(
    user: CurrentUserDep, plans: LessonPlanServiceDep, query: LessonPlanQueryDep
) -> list[LessonPlanView]:
    return await plans.list_own_all(user.id, query)


@router.get("/public", response_model=Page[LessonPlanView], summary="公共教案库")
async def list_public(
    user: CurrentUserDep, plans: LessonPlanServiceDep, query: LessonPlanQueryDep
) -> Page[LessonPlanView]:
    return await plans.list_public(user.id, query)


@router.get("/public/all", response_model=list[LessonPlanView], summary="公共教案库（不分页）")
async def list_public_all(
    user: CurrentUserDep, plans: LessonPlanServiceDep, query: LessonPlanQueryDep
) -> list[LessonPlanView]:
    return await plans.list_public_all(user.id, query)


@router.post("/predict-basic", response_model=BasicPrediction, summary="按课题推断学科/年级/单元")
async def predict_basic(payload: BasicPredictionInput, plans: LessonPlanServiceDep) -> BasicPrediction:
    return await plans.predict_basic(payload)


@router.get("/{lesson_plan_id}", response_model=LessonPlanView, summary="教案详情")
async def get_one(lesson_plan_id: int, user: CurrentUserDep, plans: LessonPlanServiceDep) -> LessonPlanView:
    return LessonPlanView.model_validate(await plans.find_visible(lesson_plan_id, user.id))


@router.post("", response_model=LessonPlanView, status_code=201, summary="新建教案")
async def create(
    payload: LessonPlanCreate, user: CurrentUserDep, plans: LessonPlanServiceDep
) -> LessonPlanView:
    return LessonPlanView.model_validate(await plans.create(user.id, user.username, payload))


@router.put("/{lesson_plan_id}", response_model=LessonPlanView, summary="保存教案")
async def update(
    lesson_plan_id: int,
    payload: LessonPlanUpdate,
    user: CurrentUserDep,
    plans: LessonPlanServiceDep,
) -> LessonPlanView:
    return LessonPlanView.model_validate(await plans.update(lesson_plan_id, user.id, payload))


@router.post("/{lesson_plan_id}/copy", response_model=LessonPlanView, status_code=201, summary="复制成新教案")
async def copy(lesson_plan_id: int, user: CurrentUserDep, plans: LessonPlanServiceDep) -> LessonPlanView:
    clone = await plans.copy(lesson_plan_id, user.id, user.username)
    return LessonPlanView.model_validate(clone)


@router.patch("/{lesson_plan_id}/public", response_model=LessonPlanView, summary="公开 / 取消公开")
async def toggle_public(
    lesson_plan_id: int, user: CurrentUserDep, plans: LessonPlanServiceDep
) -> LessonPlanView:
    return LessonPlanView.model_validate(await plans.toggle_public(lesson_plan_id, user.id))


@router.delete("/{lesson_plan_id}", summary="删除教案")
async def remove(lesson_plan_id: int, user: CurrentUserDep, plans: LessonPlanServiceDep) -> dict[str, int]:
    await plans.remove(lesson_plan_id, user.id)
    return {"id": lesson_plan_id}
