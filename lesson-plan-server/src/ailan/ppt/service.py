"""课件服务：增删改查、模板导入、预览下载，以及按教案改写模板的后台任务。

导入的外部模板走"外壳 + 产物"两段式：外壳一个字都不动，每次改写都从它重做，
产物另存成新记录。这样改写可以反复试，改坏了原模板还在。

整册改写有几十次模型往返，挂在一次 HTTP 请求上等会超时，所以 `start_rewrite` 当场只完成参数校验，
真正的改写交给 `RewriteRunner` 排队跑，前端轮询任务进度。
后台任务不能用请求级会话（响应一返回就提交了），它自己开一个会话、自己提交。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.ai_chat.config_service import AssistantConfigService, AssistantConfigServiceDep
from ailan.core.db import SessionDep, SessionFactory
from ailan.core.errors import BadRequestError, ForbiddenError, NotFoundError
from ailan.core.schemas import Page, count_rows
from ailan.lesson_plans.models import LessonPlan
from ailan.llm.factory import ModelSpec, build_model
from ailan.llm.text import empty_content_hint, model_text, pick_json_object
from ailan.ppt.exporter import PPTX_MIMETYPE, export_pptx
from ailan.ppt.files import product_bytes, shell_bytes
from ailan.ppt.models import Presentation, empty_doc
from ailan.ppt.render import PptRenderer
from ailan.ppt.schemas import (
    ORIGINAL_NAME_MAX,
    PresentationCreate,
    PresentationDoc,
    PresentationQuery,
    PresentationUpdate,
    PresentationView,
    Revision,
    RewriteInput,
    RewriteReport,
    RewriteTaskView,
    SetTextOp,
    SlotIssue,
    TemplateStructure,
)
from ailan.ppt.slots import (
    PAGE_PLAN_BATCH,
    PLAN_CONCURRENCY,
    REWRITE_CONCURRENCY,
    REWRITE_PAGES_PER_BATCH,
    PageDigest,
    RewriteSlot,
    batch_slots_by_page,
    clamp_to_limit,
    count_unwritable_texts,
    dedupe_ops,
    delete_slide_indices,
    find_echoed_slots,
    is_watermark_line,
    page_digest,
    page_of_target,
    pick_deletions,
    plan_watermark_cleaning,
    rewrite_slots,
    set_text_ops,
    stray_watermark_ops,
    template_text_map,
    text_slots,
)
from ailan.ppt.template import apply_ops, parse_structure, strip_shell_watermarks
from ailan.storage.service import StorageDep, StorageService, storage_service

logger = logging.getLogger(__name__)

# asyncio 对正在运行的任务只持弱引用：不留这一份强引用，后台协程可能在跑完之前就被回收，
# 连它抛出的异常一起消失——fire-and-forget 得自己管住引用
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _spawn(coro: Coroutine[Any, Any, Any]) -> None:
    """起一个不等结果的后台任务，跑完前把它记在 `_BACKGROUND_TASKS` 里。"""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


ALL_ROWS_LIMIT = 5000
CREATOR_NAME_MAX = 20
# 报告里最多列这么多条未处理项：再多也只是把日志搬进接口，用户看不出重点
ISSUE_REPORT_LIMIT = 60
ISSUE_TEXT_LIMIT = 40

SORTABLE: dict[str, Any] = {"createdAt": Presentation.created_at, "updatedAt": Presentation.updated_at}

# 页规划只要一份页码数组，所以提示词里没有模板原文，只给摘要
PAGE_PLAN_SYSTEM = "\n".join(
    [
        "你在为一节新课决定：一份现成 PPT 模板里哪些页要删掉。"
        "给你每页摘要（slide 页码与该页文本节选）和一篇教案。",
        "该删的页：模板自身的说明页、致谢页、下载引导页、广告页；讲的内容与本教案无关的页；同一环节重复出现的多余页。",
        "其余一律保留：封面、目录、章节页、正文页、结尾页都要留，哪怕上面的文字需要整段重写。",
        "页码必须来自给定摘要里的 slide，不得编造。",
        '只返回 JSON：{"delete":[3,7]}；没有要删的就返回 {"delete":[]}',
    ]
)

REWRITE_SYSTEM = "\n".join(
    [
        '你是教学课件改写助手。用户会给你：1）一个现有 PPT 模板的"可写文本槽"列表（target 标识、kind 类型、'
        "placeholder_type 占位符类型、text 当前文本、limit 该槽最大字数）；"
        "2）这篇教案；3）本批槽位属于模板的哪几页。",
        "任务：把模板文字替换成这篇教案对应的课件内容，输出文本替换指令。规则：",
        "- op 固定为 set_text，target 必须来自给定槽位，严禁编造；清单里每个槽都给一条 op，同一槽只给一条。",
        "- 每个槽都要写出与本页教案相关的新文字，禁止原样照抄 text 里的模板原文。",
        "- 标题槽（placeholder_type 为 TITLE/CENTER_TITLE/SUBTITLE，或原文很短的单行文本框）放该页标题；"
        "正文槽（BODY/OBJECT 或多行文本框）放要点，多条要点用 \\n 分隔。",
        "- kind 为 table 的槽：text 用 \\n 分行、\\t 分列，"
        "行列数必须与当前文本完全一致，单元格内不要出现 \\n。",
        "- 按教案顺序分配内容：本批页码越靠前越对应教案的导入与新授，靠后对应练习与总结；"
        "同一句话不要写进多个槽，也不要把整篇教案塞进一页。",
        "- 新文本不得超过该槽 limit，宁短勿长，避免撑爆文本框。",
        '- 不要新增或删除页、不要改配色版式、不要输出解释，也不要使用 {{占位符}} 或"待补充"这类写法。',
        '只返回 JSON：{"ops":[{"op":"set_text","target":"0-2","text":"……"}]}',
    ]
)


@dataclass
class RewriteTask:
    """后台改写任务的进程内状态：只服务前端进度展示，重启即失效。"""

    id: str
    ppt_id: int
    lesson_plan_id: int
    user_id: int
    username: str
    status: Literal["queued", "running", "done", "error"] = "queued"
    stage: str = "排队中"
    done: int = 0
    total: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    report: RewriteReport | None = None
    error: str | None = None

    def as_view(self) -> RewriteTaskView:
        return RewriteTaskView(
            id=self.id,
            ppt_id=self.ppt_id,
            lesson_plan_id=self.lesson_plan_id,
            status=self.status,
            stage=self.stage,
            done=self.done,
            total=self.total,
            created_at=self.created_at,
            report=self.report,
            error=self.error,
        )


class RewriteRunner:
    """改写任务的排队与执行。

    串行而不并发：并行跑两个改写既抢模型配额，也抢 PowerPoint 单实例渲染。
    任务状态只留最近若干条，进程内存活，因此不做持久化。
    """

    # 只保留最近这么多条任务；跑完很久的任务再留着没有意义
    KEEP_TASKS = 20

    def __init__(self) -> None:
        self._tasks: dict[str, RewriteTask] = {}
        self._seq = 0
        self._queue = asyncio.Lock()

    def start(
        self, *, presentation: Presentation, payload: RewriteInput, user_id: int, username: str
    ) -> RewriteTask:
        self._seq += 1
        task = RewriteTask(
            id=f"{presentation.id}-{self._seq}",
            ppt_id=presentation.id,
            lesson_plan_id=payload.lesson_plan_id,
            user_id=user_id,
            username=username,
        )
        self._tasks[task.id] = task
        self._trim()
        _spawn(self._run_queued(task))
        return task

    def list_for_user(self, user_id: int) -> list[RewriteTaskView]:
        """我发起过的改写任务，新→旧；前端据此在卡片上显示进度。"""
        return [
            task.as_view()
            for task in sorted(
                (task for task in self._tasks.values() if task.user_id == user_id),
                key=lambda task: task.created_at,
                reverse=True,
            )
        ]

    def _trim(self) -> None:
        ordered = sorted(self._tasks.values(), key=lambda task: task.created_at)
        while len(ordered) > self.KEEP_TASKS:
            oldest = ordered.pop(0)
            if oldest.status != "running":
                self._tasks.pop(oldest.id, None)

    async def _run_queued(self, task: RewriteTask) -> None:
        async with self._queue:
            task.status = "running"
            try:
                task.report = await self._rewrite(task)
                task.status = "done"
                task.stage = "完成"
            except Exception as exc:
                logger.exception("课件改写任务 %s 失败", task.id)
                task.status = "error"
                task.stage = "失败"
                task.error = str(getattr(exc, "message", None) or exc)[:300]

    async def _rewrite(self, task: RewriteTask) -> RewriteReport:
        """清洗水印 → 页取舍 → 按页分批改写 → 写回 → 回读自检补写/清空 → 另存新记录。"""
        async with SessionFactory() as session:
            configs = AssistantConfigService(session, storage_service)
            ppt = await session.get(Presentation, task.ppt_id)
            lesson = await session.get(LessonPlan, task.lesson_plan_id)
            if ppt is None or lesson is None:
                raise NotFoundError("课件或教案不存在")
            structure = _structure_of(ppt)
            spec = await configs.spec_or_throw()

            # 两份清单：清洗要连页脚页码一起看，改写清单则把页面家具排除在外
            all_slots = text_slots(structure)
            slots = rewrite_slots(structure)
            if not slots:
                raise BadRequestError("模板中没有可改写的文本")

            # 1) 确定性识别来源水印：整槽只有水印的直接清空，混排的槽仍交给改写
            _set_stage(task, "清洗来源水印")
            clean = plan_watermark_cleaning(all_slots)

            # 2) 页取舍：清完水印就没字的废页 + 模型判定与本教案无关的页，合计不许删到一页不剩
            _set_stage(task, "规划页面取舍")
            lesson_text = _lesson_to_text(lesson)
            planned, failed_batches = await self._plan_deletions(
                spec, structure, lesson_text, clean.empty_slides, task
            )
            deleted = pick_deletions(structure.slide_count, clean.empty_slides, planned)

            # 3) 按页分批改写全部文本槽，漏槽再问，最多三轮
            work_slots = [
                slot
                for slot in slots
                if page_of_target(slot.target) not in deleted and slot.target not in clean.blank_targets
            ]
            _set_stage(task, "按教案改写课件正文", 0, len(work_slots))
            model_ops = await self._generate_ops(spec, work_slots, lesson_text, task)

            # 4) 模型没接住的混排水印槽用确定性清洗兜底；整槽水印直接清空
            covered = {op.target for op in model_ops}
            fallback_ops = [
                op
                for op in clean.text_ops
                if op.target not in covered and page_of_target(op.target) not in deleted
            ]
            blank_ops = [
                SetTextOp(target=target, text="")
                for target in clean.blank_targets
                if page_of_target(target) not in deleted
            ]
            ops = _validate_ops(all_slots, [*model_ops, *blank_ops, *fallback_ops])
            if not ops and not deleted:
                raise BadRequestError("AI 未产出有效改写，请重试")

            _set_stage(task, "写回 PPTX")
            shell = await shell_bytes(ppt, storage_service)
            buffer, first = apply_ops(shell, ops, sorted(deleted))
            applied = first.applied
            product_structure = parse_structure(buffer)
            watermark_slots = len(clean.blank_targets) + len(clean.text_ops)

            # 5) 回读产物自检：文本仍与模板逐字相同=模型照抄。第一轮单独再问一次，
            #    第二轮仍照抄就清空该框——留着模板原文比留个空框更糟。
            original_text = template_text_map(structure, deleted)
            echoed = find_echoed_slots(rewrite_slots(product_structure), original_text)
            stray = stray_watermark_ops(text_slots(product_structure))
            echoed_fixed = 0
            cleared: set[str] = set()
            for round_index in range(2):
                if not echoed and not stray:
                    break
                if round_index == 0 and echoed:
                    _set_stage(task, "补写仍照抄模板原文的文本框", 0, len(echoed))
                    fresh = _validate_ops(echoed, await self._generate_ops(spec, echoed, lesson_text, task))
                    echoed_fixed += len(fresh)
                else:
                    fresh = []
                    for slot in echoed:
                        cleared.add(slot.target)
                        fresh.append(SetTextOp(target=slot.target, text=""))
                pass_ops = dedupe_ops([*stray, *fresh])
                watermark_slots += len(stray)
                if not pass_ops:
                    break
                buffer, again = apply_ops(buffer, pass_ops)
                applied += again.applied
                product_structure = parse_structure(buffer)
                echoed = find_echoed_slots(rewrite_slots(product_structure), original_text)
                stray = stray_watermark_ops(text_slots(product_structure))

            # 6) 版式与母版层的署名逐页改写碰不到，按行单独剥掉
            buffer, removed = strip_shell_watermarks(buffer, is_watermark_line)
            watermark_slots += removed

            blanked = [
                SlotIssue(target=slot.target, text=original_text[slot.target][:ISSUE_TEXT_LIMIT])
                for slot in text_slots(product_structure)
                if not slot.text and original_text.get(slot.target)
            ][:ISSUE_REPORT_LIMIT]
            unrewritten = [
                SlotIssue(target=slot.target, text=slot.text[:ISSUE_TEXT_LIMIT]) for slot in echoed
            ][:ISSUE_REPORT_LIMIT]

            # 外壳与产物各存一份新对象：与模板行共用 key 的话，删掉模板会把改写产物一起带走
            saved = await self._save_product(
                session,
                ppt,
                lesson,
                task.username,
                shell=shell,
                data=buffer,
                structure=product_structure,
            )
            _set_stage(task, "渲染预览")
            pages = await _render_quietly(saved.id, buffer)
            await session.commit()

            return RewriteReport(
                id=saved.id,
                title=saved.title,
                pages=pages or product_structure.slide_count,
                template_pages=structure.slide_count,
                applied=applied,
                deleted_slides=first.deleted_slides,
                watermark_slots=watermark_slots,
                echoed_fixed=echoed_fixed,
                cleared_slots=len(cleared),
                page_plan_failed_batches=failed_batches,
                not_editable_texts=count_unwritable_texts(product_structure),
                unrewritten=unrewritten,
                blanked=blanked,
            )

    async def _save_product(
        self,
        session: AsyncSession,
        ppt: Presentation,
        lesson: LessonPlan,
        username: str,
        *,
        shell: bytes,
        data: bytes,
        structure: TemplateStructure,
    ) -> Presentation:
        product = Presentation(
            title=lesson.title or ppt.title,
            subject=lesson.subject or ppt.subject or "",
            grade=lesson.grade or ppt.grade or "",
            lesson_plan_id=lesson.id,
            status="done",
            outline={"sections": []},
            doc={**empty_doc(), "title": lesson.title or ""},
            source="imported",
            original_name=ppt.original_name,
            original_key=await _put_pptx(shell),
            working_key=await _put_pptx(data),
            structure=structure.model_dump(mode="json"),
            is_public=False,
            user_id=ppt.user_id,
            creator_name=username[:CREATOR_NAME_MAX],
        )
        session.add(product)
        await session.flush()
        return product

    async def _plan_deletions(
        self,
        spec: ModelSpec,
        structure: TemplateStructure,
        lesson_text: str,
        junk_slides: list[int],
        task: RewriteTask,
    ) -> tuple[list[int], int]:
        """页取舍规划：每批最多 15 页，只让模型回一份待删页码数组，批次并发。

        之前一次要回"每页 keep+focus"的完整 JSON，页数一多就被 max_tokens 砍断，
        两次都失败就静默退化成"不删页"，用户完全看不出页规划根本没跑成。
        """
        model = build_model(spec, streaming=False, max_tokens=512, thinking=False)
        digest = page_digest(structure)
        chunks = [digest[start : start + PAGE_PLAN_BATCH] for start in range(0, len(digest), PAGE_PLAN_BATCH)]
        _set_stage(task, "规划页面取舍", 0, len(chunks))
        decided: set[int] = set()
        failed = 0
        done = 0
        for start in range(0, len(chunks), PLAN_CONCURRENCY):
            group = chunks[start : start + PLAN_CONCURRENCY]
            results = await asyncio.gather(
                *[self._plan_one_batch(model, chunk, junk_slides, lesson_text) for chunk in group],
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    failed += 1
                    continue
                decided.update(index for index in result if 0 <= index < structure.slide_count)
            done += len(group)
            _set_stage(task, "规划页面取舍", done, len(chunks))
        return sorted(decided), failed

    @staticmethod
    async def _plan_one_batch(
        model: Any, chunk: list[PageDigest], junk_slides: list[int], lesson_text: str
    ) -> list[int]:
        user = (
            f"【本批每页摘要】\n{_json([item.as_prompt_row() for item in chunk])}\n\n"
            f"【已判定的无字废页（无需再判）】{_json(junk_slides)}\n\n【教案】\n{lesson_text}"
        )
        # 单次不合格再问一次；两次都不合格就让这批落进 failed，报告里如实反映
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = await model.ainvoke([SystemMessage(PAGE_PLAN_SYSTEM), HumanMessage(user)])
                return _delete_indices(response)
            except BadRequestError as exc:
                last_error = exc
        raise last_error or BadRequestError("页面取舍规划未返回有效 JSON")

    async def _generate_ops(
        self, spec: ModelSpec, slots: list[RewriteSlot], lesson_text: str, task: RewriteTask
    ) -> list[SetTextOp]:
        """按页分批让模型产出 ops，并对没覆盖到的槽补写。

        单次提交整份模板（样例 11 页就有 91 个槽）会让输出预算不够，且模型对靠后页面覆盖率骤降；
        改写是填空映射任务，不需要思考模式——思考内容会把 max_tokens 吃光导致正文为空。
        批次之间互不依赖，并发跑完整册的墙钟时间才压得下来，串行时几十页要等十几分钟。
        """
        model = build_model(spec, streaming=False, max_tokens=4096, thinking=False)
        page_count = max((page_of_target(slot.target) or 0) + 1 for slot in slots) if slots else 0
        ops: list[SetTextOp] = []
        pending = slots
        last_error: BaseException | None = None

        # 首批 + 最多两轮补写：补写只递未覆盖的槽，直到没有新增或轮次用尽
        for round_index in range(3):
            if not pending:
                break
            before = len(ops)
            batches = batch_slots_by_page(pending, REWRITE_PAGES_PER_BATCH)
            for start in range(0, len(batches), REWRITE_CONCURRENCY):
                group = batches[start : start + REWRITE_CONCURRENCY]
                results = await asyncio.gather(
                    *[
                        self._ops_for_batch(model, batch, lesson_text, page_count, round_index)
                        for batch in group
                    ],
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        last_error = result
                    else:
                        ops.extend(result)
                _set_stage(task, task.stage, len({op.target for op in ops}), len(slots))
            covered = {op.target for op in ops}
            pending = [slot for slot in slots if slot.target not in covered]
            if len(ops) == before:
                break
        if not ops:
            raise BadRequestError(
                str(getattr(last_error, "message", None) or last_error or "AI 未产出有效改写，请重试")
            )
        return ops

    @staticmethod
    async def _ops_for_batch(
        model: Any, batch: list[RewriteSlot], lesson_text: str, page_count: int, round_index: int
    ) -> list[SetTextOp]:
        pages = sorted({page_of_target(slot.target) or 0 for slot in batch})
        page_list = "、".join(f"第 {page + 1} 页" for page in pages)
        user = (
            ("上一轮这些槽位没有指令，本轮必须逐条补齐。\n" if round_index else "")
            + f"【本批页码】{page_list}（模板共 {page_count} 页）\n\n"
            + f"【本批模板可写文本槽】\n{_json([_slot_row(slot) for slot in batch])}\n\n"
            + f"【教案】\n{lesson_text}\n\n"
            + "请只针对上面列出的槽位输出替换指令 JSON。"
        )
        # 模型偶发吐散文或截断，重试一次；两次都不合格才把最后一次的原始返回片段报出去
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = await model.ainvoke([SystemMessage(REWRITE_SYSTEM), HumanMessage(user)])
                ops = set_text_ops(_ops_payload(response))
                if ops:
                    # 只取文本指令：删页是页规划阶段的决定，这里不接受模型顺手删页
                    return ops
                last_error = BadRequestError("AI 未产出有效改写内容，请重试")
            except BadRequestError as exc:
                last_error = exc
        raise last_error or BadRequestError("AI 未产出有效改写内容，请重试")


class PresentationService:
    def __init__(
        self,
        session: AsyncSession,
        configs: AssistantConfigService,
        storage: StorageService,
        renderer: PptRenderer,
        runner: RewriteRunner,
    ) -> None:
        self.session = session
        self.configs = configs
        self.storage = storage
        self.renderer = renderer
        self.runner = runner

    # ── 查询 ──

    async def list_accessible(self, user_id: int, query: PresentationQuery) -> Page[PresentationView]:
        """课件库：自己建的，加上别人公开的。"""
        statement = self._filtered(
            select(Presentation).where(
                (Presentation.user_id == user_id)
                | ((Presentation.is_public.is_(True)) & (Presentation.user_id != user_id))
            ),
            query,
        )
        return await self._page(statement, query)

    async def list_mine(self, user_id: int, query: PresentationQuery) -> list[PresentationView]:
        statement = self._filtered(select(Presentation).where(Presentation.user_id == user_id), query)
        rows = await self.session.scalars(self._ordered(statement, query).limit(ALL_ROWS_LIMIT))
        return [_view(ppt) for ppt in rows]

    async def list_public(self, user_id: int) -> list[PresentationView]:
        """公共课件库：公开的、且不是自己上传的那份。"""
        rows = await self.session.scalars(
            select(Presentation)
            .where(Presentation.is_public.is_(True), Presentation.user_id != user_id)
            .order_by(desc(Presentation.updated_at))
            .limit(ALL_ROWS_LIMIT)
        )
        return [_view(ppt) for ppt in rows]

    async def get(self, presentation_id: int) -> Presentation:
        ppt = await self.session.get(Presentation, presentation_id)
        if ppt is None:
            raise NotFoundError("课件不存在")
        return ppt

    async def get_visible(self, presentation_id: int, user_id: int) -> Presentation:
        """详情、预览、下载共用的口径：本人建的或已公开的。这里放行公开件，改与删走 `get_owned`。"""
        ppt = await self.get(presentation_id)
        if ppt.user_id != user_id and not ppt.is_public:
            raise ForbiddenError("无权查看此课件")
        return ppt

    async def get_owned(self, presentation_id: int, user_id: int) -> Presentation | None:
        """Agent 工具内部使用：只允许操作本人记录，越权返回 null。"""
        ppt = await self.session.get(Presentation, presentation_id)
        return ppt if ppt is not None and ppt.user_id == user_id else None

    async def detail(self, presentation_id: int, user_id: int) -> PresentationView:
        return _view(await self.get_visible(presentation_id, user_id))

    # ── 增删改 ──

    async def create(self, user_id: int, username: str, payload: PresentationCreate) -> PresentationView:
        doc = payload.doc or PresentationDoc(title=payload.title)
        ppt = Presentation(
            title=payload.title,
            subject=payload.subject or "",
            grade=payload.grade or "",
            lesson_plan_id=payload.lesson_plan_id,
            status="outline",
            outline={"sections": []},
            doc=doc.model_dump(mode="json"),
            source="generated",
            is_public=payload.is_public,
            user_id=user_id,
            creator_name=username[:CREATOR_NAME_MAX],
        )
        self.session.add(ppt)
        await self.session.flush()
        return _view(ppt)

    async def update(
        self, presentation_id: int, user_id: int, payload: PresentationUpdate
    ) -> PresentationView:
        ppt = await self.get(presentation_id)
        if ppt.user_id != user_id:
            raise ForbiddenError("无权编辑此课件")
        fields = payload.model_dump(exclude_unset=True)
        if (doc := payload.doc) is not None and "doc" in fields:
            # JSONB 要的是纯字典，模型对象直接塞进去会在 flush 时才炸，这里提前转
            ppt.doc = doc.model_dump(mode="json")
            del fields["doc"]
        for key, value in fields.items():
            if value is not None:
                setattr(ppt, key, value)
        await self.session.flush()
        return _view(ppt)

    async def remove(self, presentation_id: int, user_id: int) -> dict[str, int]:
        ppt = await self.get(presentation_id)
        if ppt.user_id != user_id:
            raise ForbiddenError("无权删除此课件")
        keys = [ppt.original_key, ppt.working_key]
        await self.session.delete(ppt)
        # 先删行再删对象：反过来一旦对象删失败，库里就留下一条永远取不到文件的记录
        await self.session.flush()
        for key in keys:
            if key:
                await self.storage.remove(key)
        return {"id": presentation_id}

    async def copy(self, presentation_id: int, user_id: int, username: str) -> PresentationView:
        source = await self.get(presentation_id)
        if source.user_id != user_id and not source.is_public:
            raise ForbiddenError("该课件不可复制")
        copy = Presentation(
            title=f"{source.title}（副本）",
            subject=source.subject,
            grade=source.grade,
            lesson_plan_id=source.lesson_plan_id,
            status=source.status,
            outline=_deep_copy(source.outline),
            doc=_deep_copy(source.doc),
            source=source.source,
            original_name=source.original_name,
            structure=_deep_copy(source.structure),
            is_public=False,
            user_id=user_id,
            creator_name=username[:CREATOR_NAME_MAX],
        )
        if source.source == "imported":
            # 副本另存新对象：两行共用一个 key 的话，删掉任意一条会把另一条的文件一起带走
            copy.original_key = await _put_pptx(await shell_bytes(source, self.storage))
            working = await product_bytes(source, self.storage)
            if working:
                copy.working_key = await _put_pptx(working)
        self.session.add(copy)
        await self.session.flush()
        return _view(copy)

    async def toggle_public(self, presentation_id: int, user_id: int) -> PresentationView:
        ppt = await self.get(presentation_id)
        if ppt.user_id != user_id:
            raise ForbiddenError("无权操作此课件")
        ppt.is_public = not ppt.is_public
        await self.session.flush()
        return _view(ppt)

    # ── 模板导入、预览与下载 ──

    async def import_template(
        self, user_id: int, username: str, original_name: str, data: bytes
    ) -> PresentationView:
        try:
            structure = parse_structure(data)
        except (ValueError, KeyError) as exc:
            raise BadRequestError(f"无法解析该 PPTX：{exc}") from exc
        ppt = Presentation(
            title=original_name.removesuffix(".pptx") or "导入的课件",
            subject="",
            grade="",
            status="done",
            outline={"sections": []},
            doc=empty_doc(),
            source="imported",
            original_name=original_name[:ORIGINAL_NAME_MAX],
            original_key=await _put_pptx(data),
            structure=structure.model_dump(mode="json"),
            is_public=False,
            user_id=user_id,
            creator_name=username[:CREATOR_NAME_MAX],
        )
        self.session.add(ppt)
        await self.session.flush()
        # 整册渲染排进后台队列慢慢跑：几十页要等上半分钟，导入接口没必要陪着等，
        # 前端真要预览时会自动并入这次正在跑的渲染
        _spawn(_render_quietly(ppt.id, data, "original"))
        return _view(ppt)

    async def preview_page(self, presentation_id: int, user_id: int, page: int, revision: Revision) -> Path:
        """取某页预览图路径；缓存缺失时按对应版本现场渲染。

        要"改写产物"却没有产物就退回外壳：还没改写过是正常状态，不该因此报错。
        """
        ppt = await self.get_visible(presentation_id, user_id)
        render_revision: Revision = (
            "working" if revision == "working" and ppt.has_working_file else "original"
        )
        cached = self.renderer.cached_path(presentation_id, render_revision, page)
        if cached:
            return cached
        if render_revision == "working":
            data = await product_bytes(ppt, self.storage)
        else:
            data = await shell_bytes(ppt, self.storage)
        if data is None:
            raise NotFoundError("预览不存在")
        await self.renderer.render(presentation_id, render_revision, data)
        cached = self.renderer.cached_path(presentation_id, render_revision, page)
        if cached is None:
            raise NotFoundError("预览页不存在")
        return cached

    async def export_doc(self, presentation_id: int, user_id: int) -> tuple[bytes, str]:
        """把幻灯片文档实时导出成 pptx。"""
        ppt = await self.get_visible(presentation_id, user_id)
        return export_pptx(ppt.doc), f"{ppt.title or '幻灯片'}.pptx"

    async def download(self, presentation_id: int, user_id: int) -> tuple[bytes, str]:
        """下载当前 pptx：导入记录取改写产物（无则外壳），从零生成的实时导出。"""
        ppt = await self.get_visible(presentation_id, user_id)
        if ppt.source == "imported":
            working = await product_bytes(ppt, self.storage)
            data = working if working is not None else await shell_bytes(ppt, self.storage)
            base = ppt.title if working else (ppt.original_name or "").removesuffix(".pptx") or ppt.title
            return data, f"{base}.pptx"
        return export_pptx(ppt.doc), f"{ppt.title or '幻灯片'}.pptx"

    # ── AI 改写 ──

    async def start_rewrite(
        self, presentation_id: int, user_id: int, username: str, payload: RewriteInput
    ) -> RewriteTaskView:
        """启动一次模板改写：校验当场做完（参数不对直接 400），真正的改写排进后台队列。"""
        ppt = await self.get(presentation_id)
        if ppt.user_id != user_id:
            raise NotFoundError("课件不存在")
        if ppt.source != "imported":
            raise BadRequestError("该课件不是导入模板，不能使用模板改写")
        _structure_of(ppt)
        if not ppt.original_key and not ppt.original_file:
            raise BadRequestError("模板原文件缺失")
        lesson = await self.session.get(LessonPlan, payload.lesson_plan_id)
        if lesson is None:
            raise NotFoundError("教案不存在")
        # 提前取模型配置：没配 Key 就当场报 503，别让用户排队几分钟才看到任务失败
        await self.configs.spec_or_throw()
        return self.runner.start(
            presentation=ppt, payload=payload, user_id=user_id, username=username
        ).as_view()

    def list_rewrite_tasks(self, user_id: int) -> list[RewriteTaskView]:
        return self.runner.list_for_user(user_id)

    # ── 内部 ──

    def _filtered(self, statement: Select, query: PresentationQuery) -> Select:
        if query.subject:
            statement = statement.where(Presentation.subject.ilike(f"%{query.subject}%"))
        if query.grade:
            statement = statement.where(Presentation.grade == query.grade)
        if query.keyword:
            statement = statement.where(Presentation.title.ilike(f"%{query.keyword}%"))
        return statement

    def _ordered(self, statement: Select, query: PresentationQuery) -> Select:
        # 白名单在 SORTABLE 里：请求带来的字段名换不成任意列，退默认排序而不是拼进 SQL
        order = query.order_by(SORTABLE)
        if order:
            return statement.order_by(*order)
        return statement.order_by(desc(Presentation.updated_at))

    async def _page(self, statement: Select, query: PresentationQuery) -> Page[PresentationView]:
        total = await count_rows(self.session, statement)
        rows = await self.session.scalars(
            self._ordered(statement, query).offset(query.offset).limit(query.limit)
        )
        return Page[PresentationView](
            items=[_view(ppt) for ppt in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )


def _structure_of(ppt: Presentation) -> TemplateStructure:
    """取出模板结构；老数据或半截导入会缺，缺了就让用户重新导入。"""
    if not ppt.structure:
        raise BadRequestError("模板结构缺失，请重新导入")
    try:
        structure = TemplateStructure.model_validate(ppt.structure)
    except ValueError as exc:
        raise BadRequestError(f"模板结构已损坏，请重新导入：{exc}") from exc
    if not structure.slides:
        raise BadRequestError("模板结构缺失，请重新导入")
    return structure


def _lesson_to_text(lesson: LessonPlan) -> str:
    content = lesson.content or {}
    values = content.get("values") or {}
    lines = []
    for field_def in content.get("fields") or []:
        value = values.get(field_def.get("id"))
        if value is not None and str(value).strip():
            lines.append(f"【{field_def.get('label')}】{str(value).strip()}")
    return (
        f"教案标题：{lesson.title}\n学科：{lesson.subject or ''}　年级：{lesson.grade or ''}\n"
        + "\n".join(lines)
    )


def _validate_ops(slots: list[RewriteSlot], raw: list[SetTextOp]) -> list[SetTextOp]:
    """校验：target 必须存在、去重、只认 set_text，长度超预算时按句读收口。"""
    slot_map = {slot.target: slot for slot in slots}
    seen: set[str] = set()
    out: list[SetTextOp] = []
    for op in raw:
        slot = slot_map.get(op.target)
        if slot is None or op.target in seen:
            continue
        seen.add(op.target)
        # 表格按网格取交集填充，截断反而会打乱行列对齐，只对文本槽收口
        text = (
            op.text.replace("\r\n", "\n").replace("\r", "\n")
            if slot.kind == "table"
            else clamp_to_limit(op.text, slot.limit)
        )
        out.append(SetTextOp(target=op.target, text=text))
    return out


def _ops_payload(response: Any) -> list[Any]:
    obj = _json_object(response, "改写指令")
    ops = obj.get("ops")
    if not isinstance(ops, list):
        raise BadRequestError("模型返回的 JSON 中没有 ops 数组")
    return ops


def _delete_indices(response: Any) -> list[int]:
    obj = _json_object(response, "页面取舍")
    delete = obj.get("delete")
    if not isinstance(delete, list):
        raise BadRequestError("模型返回的 JSON 中没有 delete 数组")
    return delete_slide_indices(delete)


def _json_object(response: Any, label: str) -> dict[str, Any]:
    """取出模型回复里的 JSON 对象。

    `label` 让错误信息指明是哪一类产物，便于区分空响应与散文回答。
    """
    raw = model_text(response).strip()
    obj = pick_json_object(raw)
    if obj is None:
        detail = raw[:200] or empty_content_hint(response)
        raise BadRequestError(f"模型未返回{label} JSON{_truncation_hint(response)}，实际返回：{detail}")
    return obj


def _truncation_hint(response: Any) -> str:
    """finish_reason=length 说明 JSON 被 max_tokens 砍断，与"模型吐散文"是两种修法。"""
    metadata = getattr(response, "response_metadata", None) or {}
    return "（输出达最大 token 被截断）" if metadata.get("finish_reason") == "length" else ""


def _slot_row(slot: RewriteSlot) -> dict[str, Any]:
    return {
        "target": slot.target,
        "kind": slot.kind,
        "placeholder_type": slot.placeholder_type,
        "text": slot.text,
        "limit": slot.limit,
    }


def _json(value: Any) -> str:
    """中文原样进提示词：转义成 \\uXXXX 会让模型多读一层，也更难核对。"""
    return json.dumps(value, ensure_ascii=False)


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value)) if value is not None else None


async def _put_pptx(data: bytes) -> str:
    """一份 pptx 落一次对象，返回新 key。"""
    key = storage_service.new_key("ppt", PPTX_MIMETYPE)
    await storage_service.put(key, data, PPTX_MIMETYPE)
    return key


async def _render_quietly(presentation_id: int, data: bytes, revision: Revision = "working") -> int:
    """渲染失败不该让改写失败：预览缺了随时能重渲，报告里的页数退回模板页数即可。"""
    try:
        return await renderer.render(presentation_id, revision, data)
    except Exception as exc:
        logger.warning("课件 %s 预览渲染失败: %s", presentation_id, exc)
        return 0


def _set_stage(task: RewriteTask, stage: str, done: int = 0, total: int = 0) -> None:
    task.stage = stage
    task.done = done
    task.total = total


def _view(ppt: Presentation) -> PresentationView:
    """模型列 → 对外视图；`has_working` 在模型上叫 `has_working_file`，只能补一刀。"""
    return PresentationView.model_validate(ppt).model_copy(update={"has_working": ppt.has_working_file})


# 渲染器与改写队列都是进程级单例：它们的状态跨请求存活，挂在每请求新建的服务上等于没缓存
renderer = PptRenderer()
rewrite_runner = RewriteRunner()


def get_presentation_service(
    session: SessionDep, configs: AssistantConfigServiceDep, storage: StorageDep
) -> PresentationService:
    return PresentationService(session, configs, storage, renderer, rewrite_runner)


PresentationServiceDep = Annotated[PresentationService, Depends(get_presentation_service)]
