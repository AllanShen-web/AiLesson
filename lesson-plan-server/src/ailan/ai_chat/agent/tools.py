"""Agent 可调用的工具：把平台能力一条条摊开，让模型自己挑。

工具集每轮重新拼装。表单页里能填当前表单、列表页里能建教案，注册多了模型会串线
（同一件事有两个工具、两套口径），注册少了它只能对用户说"做不到"。每条工具的准入条件都写死在
这里，判据必须和 `prompt.build_system_prompt` 的片段开关一致——提示词里讲了却没注册的工具，
模型会凭空尝试；注册了却没讲规则的工具，模型会拿着它乱用。

参数一律用 `Annotated[..., Field(description=...)]` 声明，校验交给框架，并把"哪一项不合格"
原样回给模型。只回一句"工具执行出错，请稍后再试"是没用的：模型看不到原因就会一字不改地
重发同一个调用，实测连错三次后直接放弃，整条流程静默卡死。

工具返回给模型的是文字（它读的是清单和编号），前端要的却是结构，所以字段内容、产出文件、
选择卡片、幻灯片页面这些"给用户看的东西"都同时以事件推出去，一句 `return` 只管让模型知道
"这一步做完了、下一步该做什么"。新建教案那条链的顺序约束也落在工具层：模型对流程时灵时不灵，
乱序调用直接打回比在提示词里祈祷可靠。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.ai_chat.agent.plan_flow import PlanFlow, TemplateChoice
from ailan.ai_chat.agent.prompt import MAX_PPT_OUTLINE_SECTIONS, FieldGroups, group_rules
from ailan.ai_chat.files.generator import ExportedFile, generate_file, normalize_fields
from ailan.ai_chat.files.layout import LayoutField
from ailan.ai_chat.models import GeneratedFile
from ailan.ai_chat.schemas import AgentChatInput
from ailan.ai_chat.sse import Emit
from ailan.core.errors import AppError
from ailan.lesson_plans.models import LessonPlan
from ailan.ppt.exporter import normalize_slide
from ailan.ppt.models import Presentation
from ailan.rag.extract import extract_document_text
from ailan.rag.schemas import RagHit
from ailan.resources.files import read_bytes
from ailan.resources.models import Resource
from ailan.storage.service import StorageService
from ailan.templates.models import Template

logger = logging.getLogger(__name__)

# 单次回给模型的正文字数上限，与对话附件的抽取保持同一量级：再长就把上下文挤没了
CONTENT_CHAR_LIMIT = 8_000
# 私有模板不超过这个条数时，把公有模板一起列进候选（标清是公有）。
# 只有一两条自己的模板时没得挑，公有模板正好补上候选；自己的模板一多就不掺进来，
# 免得几十条私有里再插几条公有的，用户在卡片里翻着费劲
PUBLIC_TEMPLATES_WHEN_MINE_LTE = 4
# 选择卡片一轮最多三问、一问最多三十个选项：超出只是模型乱填，前端也摆不下
MAX_CHOICE_OPTIONS = 30
MAX_CHOICE_QUESTIONS = 3

# 搜索结果的条数上限：八条足够感知风格，再多只是占上下文
WEB_HIT_LIMIT = 8
WEB_SEARCH_TIMEOUT_SECONDS = 8.0
# 有些搜索页会按 UA 决定给不给 HTML 版，缺这个请求头拿回来的是空壳
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

RagSearch = Callable[..., Awaitable[list[RagHit]]]

# 11 种版式，与导出器认的一致：大纲阶段就把版式分布规划好，避免整份课件每页雷同
SLIDE_LAYOUTS = (
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
)
LAYOUT_LIST_HINT = "/".join(SLIDE_LAYOUTS)


@dataclass(frozen=True)
class WebHit:
    """一条网页结果：只取标题、链接、摘要，不下载正文与二进制文件。"""

    title: str
    url: str
    snippet: str


@dataclass
class ToolContext:
    """一轮请求里工具能碰到的一切。

    `session` 是请求会话本身：工具节点逐个执行调用，不存在并发写同一份 ORM 对象，
    所以共享一个会话是安全的，也让本轮的教案、课件、文件跟着消息一起提交。
    可选能力留 `None` 就等于"这条链本轮不成立"，注册工具时按它筛。
    """

    session: AsyncSession
    user_id: int
    username: str
    storage: StorageService
    plan_flow: PlanFlow
    rag_search: RagSearch | None = None

    async def save_file(self, file: ExportedFile) -> GeneratedFile:
        """落一条助手产出文件记录：配了对象存储就只留 key，本地环境退回 data 列。

        只写 `ai_files` 这一张表，不往资料库塞第二份二进制——产出文件由「我的文件」列表单独管理。
        """
        row = GeneratedFile(
            user_id=self.user_id,
            file_name=file.file_name,
            mimetype=file.mimetype,
            size_bytes=len(file.content),
            format=file.format,
        )
        if self.storage.configured:
            key = self.storage.new_key("ai-file", file.mimetype, file.file_name)
            await self.storage.put(key, file.content, file.mimetype)
            row.storage_key = key
        else:
            row.data = file.content
        self.session.add(row)
        await self.session.flush()
        return row


# ── 工具入参的形状 ──
# 这些是「模型写给我们」的结构，与 `ppt.schemas` / 接口入参分开：后者出浏览器要 camelCase，
# 而提示词里给模型看的键名一律 snake_case，两套契约不能共用一个类，否则模型照着提示词写、
# 按另一个口径校验，参数校验失败的原因它自己看不到。


class ChoiceOption(BaseModel):
    label: Annotated[str, Field(description="选项文字，用户点选时原样发回，要短（不超过 20 字）")]
    description: Annotated[str | None, Field(description="一句话说明，显示在选项文字后面")] = None


class ChoiceQuestion(BaseModel):
    kind: Annotated[
        Literal["template", "method", "other"],
        Field(description='这一问在问什么：选教案模板传 "template"，选创建方式传 "method"，其余传 "other"'),
    ]
    question: Annotated[str, Field(description="显示在该问顶部的问题")]
    options: Annotated[
        list[ChoiceOption],
        Field(
            min_length=1,
            description="该问的选项，不限条数；只有一个候选也要列出来让用户点选确认，"
            "label 必须逐字照抄清单里的名字，不许自行增删后缀",
        ),
    ]


class OutlinePage(BaseModel):
    title: Annotated[str, Field(description="该页标题")]
    points: Annotated[list[str] | None, Field(description="该页要点列表")] = None
    layout: Annotated[
        str | None,
        Field(description=f"该页建议版式，按内容性质挑选、避免雷同：{LAYOUT_LIST_HINT}"),
    ] = None


class BigNumberSpec(BaseModel):
    value: Annotated[str, Field(description="核心数字，如 3.14、56、90%")]
    unit: Annotated[str | None, Field(description="单位，如 万、℃、%、个")] = None
    label: Annotated[str | None, Field(description="一句话说明这个数字的含义")] = None


class ChartSeriesSpec(BaseModel):
    name: Annotated[str, Field(description="系列名，如 产量、人数")]
    data: Annotated[list[float], Field(description="与类别一一对应的数据")]


class ChartSpec(BaseModel):
    type: Annotated[
        Literal["bar", "line", "pie"], Field(description="图表类型：bar 柱状 / line 折线 / pie 饼图")
    ]
    categories: Annotated[list[str], Field(description="横轴类别（饼图为各分项名称），3~6 个")]
    series: Annotated[list[ChartSeriesSpec], Field(description="数据系列，1~2 个")]


class TimelineStepSpec(BaseModel):
    title: Annotated[str, Field(description="步骤/阶段名")]
    desc: Annotated[str | None, Field(description="该步骤简要说明")] = None


class CompareSideSpec(BaseModel):
    title: Annotated[str, Field(description="这一栏的标题")]
    bullets: Annotated[list[str], Field(description="这一栏的要点")]


class CompareSpec(BaseModel):
    left: Annotated[CompareSideSpec, Field(description="左侧")]
    right: Annotated[CompareSideSpec, Field(description="右侧")]


class ActivitySpec(BaseModel):
    name: Annotated[str, Field(description="活动名称，如 小组讨论 / 实验探究 / 角色扮演")]
    steps: Annotated[list[str], Field(description="活动步骤，3~5 步")]


class SlideSpec(BaseModel):
    layout: Annotated[
        str | None,
        Field(description=f"版式，按内容性质选：{LAYOUT_LIST_HINT}。不认识的值会退回 title-bullets"),
    ] = None
    title: Annotated[str | None, Field(description="本页标题，留空则取大纲里该页的标题")] = None
    bullets: Annotated[list[str] | None, Field(description="要点列表，每页不超过 6 条")] = None
    content: Annotated[str | None, Field(description="正文兜底（bullets 为空时使用）")] = None
    notes: Annotated[str | None, Field(description="演讲者备注")] = None
    big_number: Annotated[BigNumberSpec | None, Field(description="layout=big-number 时提供")] = None
    chart: Annotated[ChartSpec | None, Field(description="layout=chart 时提供：数据解读写进 bullets")] = None
    timeline: Annotated[
        list[TimelineStepSpec] | None, Field(description="layout=timeline 时提供：有序步骤")
    ] = None
    compare: Annotated[CompareSpec | None, Field(description="layout=compare 时提供：左右两栏")] = None
    activity: Annotated[ActivitySpec | None, Field(description="layout=activity 时提供：活动名 + 步骤")] = (
        None
    )


class ThemeSpec(BaseModel):
    """主题色板，逐色覆盖：只传用户提出要求的这几项，其余留给导出器的默认值。"""

    primary: Annotated[str | None, Field(description="主题色，6 位十六进制不带 #")] = None
    accent: Annotated[str | None, Field(description="辅助色（装饰、渐变、编号底）")] = None
    bg: Annotated[str | None, Field(description="页面底色")] = None
    title_color: Annotated[str | None, Field(description="标题色")] = None
    text_color: Annotated[str | None, Field(description="正文色")] = None
    font: Annotated[str | None, Field(description="字体，统一「微软雅黑」")] = None
    style: Annotated[Literal["plain", "band", "card"] | None, Field(description="装饰风格")] = None


# ── 联网检索 ──


async def search_web_references(query: str) -> list[WebHit]:
    """搜外部资料：Bing 为主，零结果或失败时改用 DuckDuckGo 的 HTML 版兜底。

    任何一层失败都只回空数组，由调用方降级成"跳过参考步骤继续"——风格参考是可选环节，
    不该因为外部站点抖一下就整轮失败。
    """
    headers = {"User-Agent": BROWSER_USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}
    async with httpx.AsyncClient(
        timeout=WEB_SEARCH_TIMEOUT_SECONDS, headers=headers, follow_redirects=True
    ) as client:
        for fetch in (_fetch_bing, _fetch_duckduckgo):
            try:
                hits = await fetch(client, query)
            except httpx.HTTPError:
                continue
            if hits:
                return hits
        return []


async def _fetch_bing(client: httpx.AsyncClient, query: str) -> list[WebHit]:
    response = await client.get("https://cn.bing.com/search", params={"q": query, "setlang": "zh-CN"})
    response.raise_for_status()
    hits: list[WebHit] = []
    for item in BeautifulSoup(response.text, "lxml").select("li.b_algo"):
        link = item.select_one("h2 a")
        if link is None or not link.get("href"):
            continue
        hits.append(
            WebHit(
                title=link.get_text(strip=True),
                url=str(link["href"]),
                snippet=_cell_text(item.select_one(".b_caption p")),
            )
        )
    return hits[:WEB_HIT_LIMIT]


async def _fetch_duckduckgo(client: httpx.AsyncClient, query: str) -> list[WebHit]:
    response = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
    response.raise_for_status()
    hits: list[WebHit] = []
    for item in BeautifulSoup(response.text, "lxml").select("div.result"):
        link = item.select_one(".result__a")
        if link is None or not link.get("href"):
            continue
        hits.append(
            WebHit(
                title=link.get_text(strip=True),
                url=_unwrap_redirect(str(link["href"])),
                snippet=_cell_text(item.select_one(".result__snippet")),
            )
        )
    return hits[:WEB_HIT_LIMIT]


def _cell_text(node: Any) -> str:
    return "" if node is None else node.get_text(strip=True)


def _unwrap_redirect(href: str) -> str:
    """DuckDuckGo 给的是 `.../l/?uddg=<真链接>` 这种中转地址：还原成原站链接，
    用户按摘要去核对时不会先撞到一个空白跳转页。"""
    if "uddg=" not in href:
        return href
    target = parse_qs(urlparse(href).query).get("uddg")
    return unquote(target[0]) if target else href


# ── 教案检索与读取 ──


def _visible_plans(user_id: int):
    """自己的 + 别人的公开教案：模型找"有没有现成的这一课"时，只看自己会漏掉公开库。"""
    return select(LessonPlan).where(or_(LessonPlan.user_id == user_id, LessonPlan.is_public.is_(True)))


def _labels_of(raw_fields: list[Any]) -> dict[str, str]:
    """快照里的字段定义 → id 到中文名。给模型看的正文一律用中文名，不暴露内部 id。"""
    return {
        str(item["id"]): str(item["label"])
        for item in raw_fields
        if isinstance(item, dict) and item.get("id") and item.get("label")
    }


def _readable_plan(plan: LessonPlan) -> str:
    values = (plan.content or {}).get("values") or {}
    labels = _labels_of((plan.content or {}).get("fields") or [])
    body = "\n".join(f"{labels.get(key) or key}: {value}" for key, value in values.items())
    return (
        f"标题: {plan.title}\n科目: {plan.subject or '未填'}\n年级: {plan.grade or '未填'}\n\n内容:\n{body}"
    )


def _access_error(owner_id: int, is_public: bool, user_id: int, what: str) -> str | None:
    if owner_id == user_id or is_public:
        return None
    return f"无权访问该{what}。"


# ── 表单模式：填当前这一篇 ──


def _form_tools(request: AgentChatInput, ctx: ToolContext, emit: Emit) -> list[BaseTool]:
    fields = request.available_fields or []
    groups = FieldGroups.from_request(fields)

    @tool
    async def fill_form_field(
        field_id: Annotated[str, Field(description="要填充的栏目 id，取自提示词里的可用字段清单")],
        content: Annotated[str, Field(description="要填入该栏目的完整正文，不含栏目名、编号或标题")],
    ) -> str:
        """把某一栏的内容填进当前教案表单。

        用户选中了某个表单栏目并请求生成或改写该内容时使用。内容只写正文：前端已经把栏目名显示
        在输入框上方，正文里再重复一遍用户会看到两个「教学目标」。
        """
        if not field_id or not content:
            return "错误：请同时提供 field_id 和 content。"
        field = next((item for item in fields if item.id == field_id), None)
        label = groups.display_name(field_id) if field else field_id or "未知字段"
        # 业务事件：内容直接进表单输入框，并由服务端防抖自动保存
        emit({"type": "tool", "tool_type": "fill_form_field", "field_id": field_id, "content": content})
        return f"栏目「{label}」（id: {field_id}）已填入内容（{len(content)} 字）。"

    return [fill_form_field]


# ── 检索：教案、资料库、语义片段 ──


def _search_tools(request: AgentChatInput, ctx: ToolContext, emit: Emit) -> list[BaseTool]:
    @tool
    async def search_lesson_plans(
        keyword: Annotated[str | None, Field(description="课题关键词，可选")] = None,
        subject: Annotated[str | None, Field(description="学科，可选")] = None,
        grade: Annotated[str | None, Field(description="年级，可选")] = None,
    ) -> str:
        """搜索教案列表，返回摘要（标题、学科、年级、编号），不含正文。

        用户想查找或确认"有没有某篇教案"时使用；需要某一栏的实际内容时再调用 read_lesson_plan。
        """
        statement = _visible_plans(ctx.user_id).order_by(LessonPlan.updated_at.desc())
        if keyword:
            statement = statement.where(LessonPlan.title.ilike(f"%{keyword}%"))
        if subject:
            statement = statement.where(LessonPlan.subject.ilike(f"%{subject}%"))
        if grade:
            statement = statement.where(LessonPlan.grade.ilike(f"%{grade}%"))
        plans = list(await ctx.session.scalars(statement))
        if not plans:
            return "未找到匹配的教案。"
        return "\n".join(
            f"- {plan.title} | 科目: {plan.subject or '未填'} | 年级: {plan.grade or '未填'}"
            f" | {'公开' if plan.is_public else '私有'} | [内部编号 {plan.id}]"
            for plan in plans
        )

    @tool
    async def read_lesson_plan(lesson_plan_id: Annotated[int, Field(description="教案编号")]) -> str:
        """读取某一篇教案的完整内容。

        在 search_lesson_plans 拿到编号后按需调用，或用户明确点名要看某篇教案时使用。
        """
        plan = await ctx.session.get(LessonPlan, lesson_plan_id)
        if plan is None:
            return "教案不存在。"
        denied = _access_error(plan.user_id, plan.is_public, ctx.user_id, "教案")
        if denied:
            return denied
        return _readable_plan(plan)

    @tool
    async def search_knowledge_base(
        keyword: Annotated[str | None, Field(description="文件名关键词，可选；留空则列出全部文件")] = None,
    ) -> str:
        """按文件名搜索资料库文件，返回文件名、类型、大小和编号。

        不传 keyword 就是列出全部文件。要读正文用 read_knowledge_file，
        要找某个概念或段落用 search_knowledge_chunks。
        """
        rows = list(
            await ctx.session.scalars(
                select(Resource)
                .where(or_(Resource.user_id == ctx.user_id, Resource.is_public.is_(True)))
                .order_by(Resource.created_at.desc())
            )
        )
        if not rows:
            return "资料库为空，没有可供参考的文件。"

        # 只按文件名匹配：学科/年级在新版上传里一律是空串，一旦当成硬过滤，
        # 模型带上「劳动/三年级」这类参数就必然清空结果
        matched = rows
        if keyword:
            needle = keyword.lower()
            matched = [
                item
                for item in rows
                if needle in (item.name or "").lower() or needle in (item.original_name or "").lower()
            ]
        if not matched:
            # 关键词没命中就把全量清单交回去：模型据此换真实文件名重试，而不是凭想象编编号
            return (
                f"资料库中没有文件名含「{keyword}」的文件。以下是资料库全部文件，请从中选择：\n"
                f"{_file_lines(rows)}"
            )
        return _file_lines(matched)

    @tool
    async def read_knowledge_file(resource_id: Annotated[int, Field(description="资料库文件编号")]) -> str:
        """读取资料库某个文件的正文（Word、PDF、txt 都会先抽成纯文本）。

        在 search_knowledge_base 拿到编号后按需调用。超长文件只给前若干字，
        需要后面的段落改用 search_knowledge_chunks 换关键词检索。
        """
        resource = await ctx.session.get(Resource, resource_id)
        if resource is None:
            return "文件不存在。"
        denied = _access_error(resource.user_id, resource.is_public, ctx.user_id, "文件")
        if denied:
            return denied
        return f"{_resource_head(resource)}\n\n文件内容:\n{await _resource_text(resource, ctx)}"

    tools: list[BaseTool] = [
        search_lesson_plans,
        read_lesson_plan,
        search_knowledge_base,
        read_knowledge_file,
    ]
    rag_search = ctx.rag_search
    if rag_search is not None:

        @tool
        async def search_knowledge_chunks(
            query: Annotated[str, Field(description="检索问题或关键词，用自然语言描述要找的内容")],
            top_k: Annotated[int | None, Field(description="返回片段数量，缺省 5")] = None,
        ) -> str:
            """在资料库已向量化过的文件里做语义检索，返回最相关的原文片段。

            用户的问题涉及某个文件的具体内容（概念、数据、段落细节）时优先用它，
            而不是把整个文件读进来。
            """
            try:
                hits = await rag_search(query, top_k or 5)
            except Exception as exc:  # 嵌入端点抖动只该降级，不该中断这一轮
                logger.warning("[Agent] 语义检索失败: %s", exc)
                return (
                    "语义检索暂不可用（嵌入模型调用失败）。"
                    "请改用 search_knowledge_base / read_knowledge_file 按文件名查找资料。"
                )
            if not hits:
                return (
                    "未检索到与问题语义相关的已向量化内容。可能相关资料尚未向量化，"
                    "可改用 search_knowledge_base 浏览文件列表。"
                )
            return "\n\n".join(
                f"{index}. 《{hit.resource_name}》片段（第 {hit.chunk_index + 1} 块）:\n{hit.content}"
                for index, hit in enumerate(hits, 1)
            )

        tools.append(search_knowledge_chunks)
    return tools


def _file_lines(rows: list[Resource]) -> str:
    return "\n".join(
        f"- {item.name} | {item.mimetype} | {item.size_bytes / 1024:.1f}KB"
        f" | {'公开' if item.is_public else '私有'} | [内部编号 {item.id}]"
        for item in rows
    )


def _resource_head(resource: Resource) -> str:
    # 学科/年级只在真有值时报给模型：空字段显示「未填」会诱导模型拿它们去过滤检索
    meta = [
        text
        for text in (
            f"科目: {resource.subject}" if resource.subject else "",
            f"年级: {resource.grade}" if resource.grade else "",
        )
        if text
    ]
    lines = [
        f"文件名: {resource.name} ({resource.original_name})",
        f"类型: {resource.mimetype}",
        f"大小: {resource.size_bytes / 1024:.1f}KB",
    ]
    return "\n".join(lines + meta)


async def _resource_text(resource: Resource, ctx: ToolContext) -> str:
    """文件正文，抽不出内容时给模型一句原因而不是空白：静默跳过等于让它以为自己看到了这个文件。"""
    try:
        data = await read_bytes(resource, ctx.storage)
    except AppError:
        return "(对象存储未就绪，无法读取文件内容)"
    text = extract_document_text(resource.mimetype, resource.original_name, data)
    if not text or not text.strip():
        return "(未能从该文件提取到文本内容，当前支持 Word(.docx)、PDF、txt 及纯文本格式)"
    if len(text) <= CONTENT_CHAR_LIMIT:
        return text
    return (
        f"{text[:CONTENT_CHAR_LIMIT]}\n"
        f"（注意：内容已截断，原文共 {len(text)} 字，此处仅前 {CONTENT_CHAR_LIMIT} 字；"
        "需要其他段落请改用 search_knowledge_chunks 语义检索）"
    )


# ── 导出文件 ──


def _export_tool(request: AgentChatInput, ctx: ToolContext, emit: Emit) -> list[BaseTool]:
    @tool
    async def export_lesson_plan(
        format: Annotated[str, Field(description="导出格式：docx/word、pdf、pptx/PPT、md/markdown、txt")],
        lesson_plan_id: Annotated[int | None, Field(description="教案编号，导出数据库已有教案时必传")] = None,
        title: Annotated[str | None, Field(description="临时稿的标题，可选")] = None,
        content: Annotated[str | None, Field(description="纯文本/markdown 临时内容，可选")] = None,
        fields: Annotated[
            dict[str, str] | None, Field(description="临时稿的栏目内容，key 为栏目 id、value 为正文")
        ] = None,
        form_info: Annotated[
            dict[str, str] | None, Field(description="临时稿的教案信息（topic/subject/grade/unit）")
        ] = None,
    ) -> str:
        """把教案导出成文件（docx / pdf / pptx / md / txt）。

        正文一律由系统自己取（数据库快照 + 当前表单实时内容），严禁把整篇内容拼进 fields 或
        content 传回来——一次回复塞不下整篇，工具调用会被输出长度上限截断，结果是文件根本没生成。
        在表单页导出当前这一篇只传 format 即可；导出别的教案才传 lesson_plan_id。
        """
        target_id = lesson_plan_id if lesson_plan_id else request.lesson_plan_id
        snapshot, err = await _export_snapshot(ctx, target_id)
        if err:
            return err

        layout_fields = snapshot["layout_fields"]
        labels = snapshot["labels"]
        # 编辑器上下文里可能有快照缺失的字段定义（新建教案还没保存过）
        if not layout_fields and request.available_fields:
            layout_fields = [LayoutField.from_json(item.model_dump()) for item in request.available_fields]
            labels.update({item.id: item.label for item in request.available_fields if item.label})

        values = dict(snapshot["values"])
        # 表单页的实时内容永远优先于库内快照：没点过保存的新增页只有这一份。
        # 只在"导出的就是编辑器里这一篇"时合并，用户指名导另一篇时不被当前表单污染。
        if not target_id or target_id == request.lesson_plan_id:
            values.update({key: value for key, value in (request.lesson_plan_content or {}).items() if value})
        if not values:
            # 库里没有、表单里也没有（对话里现写的临时稿），才采用模型传来的内容
            values.update({key: str(value) for key, value in normalize_fields(fields).items() if value})

        file = generate_file(
            format=format,
            title=snapshot["title"] or title or (request.form_context or {}).get("topic") or "教案",
            fields=values,
            field_labels=labels,
            layout_fields=layout_fields,
            content=content or "",
            form_info=snapshot["form_info"] or form_info or request.form_context,
        )
        logger.info("[Agent] 导出教案：%s %s %s 字节", file.format, file.file_name, len(file.content))

        try:
            row = await ctx.save_file(file)
        except AppError as exc:
            logger.warning("[Agent] 产出文件保存失败: %s", exc)
            return "错误：文件保存失败，请稍后再试"

        emit(
            {
                "type": "file",
                "file_id": row.id,
                "file_name": file.file_name,
                "format": file.format,
                "size_bytes": len(file.content),
            }
        )
        return f"文件「{file.file_name}」已生成（{len(file.content) / 1024:.1f}KB）。"

    return [export_lesson_plan]


async def _export_snapshot(ctx: ToolContext, plan_id: int | None) -> tuple[dict[str, Any], str | None]:
    """读数据库里这一篇的真实内容与排版依据；没有指定教案时返回空快照，由表单上下文补齐。"""
    empty = {"values": {}, "labels": {}, "layout_fields": [], "title": "", "form_info": {}}
    if plan_id is None:
        return empty, None
    plan = await ctx.session.get(LessonPlan, plan_id)
    if plan is None:
        return empty, "错误：教案不存在。请先调用 search_lesson_plans 搜索正确的教案。"
    denied = _access_error(plan.user_id, plan.is_public, ctx.user_id, "教案")
    if denied:
        return empty, f"错误：无权{denied}"
    raw_fields = (plan.content or {}).get("fields") or []
    return (
        {
            "values": {k: str(v) for k, v in ((plan.content or {}).get("values") or {}).items()},
            "labels": _labels_of(raw_fields),
            # 排版必须用快照里的字段定义：模板后来改了字段也不能让已经写好的教案变了样
            "layout_fields": [LayoutField.from_json(item) for item in raw_fields],
            "title": plan.title or "",
            "form_info": {
                "topic": plan.title or "",
                "subject": plan.subject or "",
                "grade": plan.grade or "",
            },
        },
        None,
    )


# ── 新建教案流程（表单之外）──


def _new_plan_tools(request: AgentChatInput, ctx: ToolContext, emit: Emit) -> list[BaseTool]:
    """「选模板 → 选创建方式 → 跳转新增页或建档生成」这条链的工具。

    整条链只在用户不在表单页时成立：已经在新增/编辑页上说"生成教案"，只可能是往当前表单里填，
    再弹一次选模板卡片就是把两条流程搅在一起。
    """
    flow = ctx.plan_flow
    session = ctx.session

    def order_guard() -> str | None:
        """跳转与建档共用的顺序校验：模板和创建方式都必须由用户在卡片里点选出来。"""
        if not flow.method_chosen:
            return (
                "错误：用户还没有通过选项卡片选定创建方式，不能继续。请按「选模板 → 选创建方式」的顺序推进，"
                "先 ask_choice 一次弹出这两问，等用户点选后再回来。"
            )
        if flow.template is None:
            return (
                "错误：用户还没有通过选项卡片选定模板，不能继续。请先 ask_choice 弹「选模板」这一问"
                "（选项取自 list_lesson_templates 的清单），用户点选后再回来。"
            )
        return None

    def template_guard(template_id: int) -> str | None:
        """跳转与建档共用的一句提醒：模板必须由用户点选，编号只认服务端解析出来的那一个。"""
        chosen = flow.template
        if chosen is not None and template_id != chosen.id:
            return (
                f"错误：用户选定的模板是「{chosen.name}」（内部编号 {chosen.id}），"
                f"template_id 必须传 {chosen.id}；"
                "若用户已改选别的模板，请先 ask_choice 重新弹「选模板」这一问确认。"
            )
        return None

    @tool
    async def list_lesson_templates() -> str:
        """列出当前用户可用的教案模板：他自己的私有模板，私有不满 5 条时一并列出公有模板。

        新建教案的第一步，务必先调用本工具拿清单，严禁凭记忆罗列模板。
        公有模板只能经 copy_template 拷成自己的一份后使用。
        """
        mine = list(
            await session.scalars(
                select(Template).where(Template.user_id == ctx.user_id).order_by(Template.created_at.desc())
            )
        )
        shared: list[Template] = []
        if len(mine) <= PUBLIC_TEMPLATES_WHEN_MINE_LTE:
            shared = list(
                await session.scalars(
                    select(Template)
                    .where(or_(Template.is_public.is_(True), Template.is_official.is_(True)))
                    .order_by(Template.created_at.desc())
                )
            )
        # 自己拷来的那份可能同时命中公开条件：去重，别在卡片里出现两遍
        extras = [item for item in shared if not any(mine_item.id == item.id for mine_item in mine)]
        if not mine and not extras:
            return "模板库里还没有任何模板。请告知用户先到「模板管理」页面创建模板，再来新建教案。"

        def line_of(template: Template, tag: str) -> str:
            count = len((template.content or {}).get("fields") or [])
            return f"- {template.name} | 共 {count} 个栏目 | {tag} | [内部编号 {template.id}]"

        listing = "\n".join(
            [line_of(item, "我的模板") for item in mine]
            + [line_of(item, "公有模板：点选后需先拷贝") for item in extras]
        )
        flow.note_templates([TemplateChoice(id=item.id, name=item.name) for item in mine + extras])
        flow.set_step("listed")
        return (
            f"可用模板：\n{listing}\n\n"
            '请调用 ask_choice 把「选模板」和「选创建方式」两问一次弹出：模板问的 kind 传 "template"，'
            "每个模板一个选项，label 必须逐字用上面清单里的模板名（系统按 label 对号解析内部编号，"
            "加任何前后缀都会解析失败），栏目数和「公有模板」写进 description；"
            '方式问的 kind 传 "method"，选项见新建教案规则。'
            "用户点选了标「公有模板」的那一项时，先调用 copy_template 把它拷进用户自己的模板库"
            "（拷贝结果会给出新编号），再继续建档或跳转。"
            "每次新建都要问模板：即使用户消息里报了某个模板名，也要请他点选确认——"
            "系统只认点选结果，替用户挑的模板会被工具打回。"
            "只有一个模板也要把它作为唯一选项列出来请用户点选，不许因为没得选就跳过这一问。"
        )

    @tool
    async def copy_template(
        template_id: Annotated[int, Field(description="要拷贝的公有模板编号，来自 list_lesson_templates")],
    ) -> str:
        """把一个公有或官方模板拷贝为用户自己的私有模板。

        只在新建教案流程中、用户选择了公有模板时调用；拷贝完成后用新编号继续往下走。
        """
        source = await session.get(Template, template_id)
        if source is None:
            return "错误：模板不存在。请先调用 list_lesson_templates 获取正确的模板编号。"
        if source.user_id == ctx.user_id:
            return "该模板已在用户自己的模板库里，无需拷贝，直接使用。"
        if not source.is_public and not source.is_official:
            return "错误：该模板未公开，无法拷贝。"

        existing = set(
            (await session.scalars(select(Template.name).where(Template.user_id == ctx.user_id))).all()
        )
        copy_name = source.name
        # 拷进来的同名模板要避让：直接重名会让用户在列表里分不清哪一份是自己的
        if copy_name in existing:
            suffix = 2
            while f"{source.name}（{suffix}）" in existing:
                suffix += 1
            copy_name = f"{source.name}（{suffix}）"

        copy = Template(
            name=copy_name,
            user_id=ctx.user_id,
            creator_name=ctx.username,
            is_official=False,
            is_public=False,
            content=source.content,
        )
        session.add(copy)
        await session.flush()
        logger.info("[Agent] 拷贝模板 %s -> %s %s", source.id, copy.id, copy_name)
        flow.choose_template(TemplateChoice(id=copy.id, name=copy_name))
        return f"模板「{copy_name}」已拷贝到用户的模板库（内部编号 {copy.id}）。请用该编号继续新建教案流程。"

    @tool
    async def create_lesson_plan(
        template_id: Annotated[int, Field(description="模板编号，必须与用户刚点选的那个一致")],
        title: Annotated[str, Field(description="课题名称")],
        subject: Annotated[str | None, Field(description="学科，可选")] = None,
        grade: Annotated[str | None, Field(description="年级，可选")] = None,
    ) -> str:
        """按模板新建一篇教案记录（直接出文件路线的第一步），返回编号和待填栏目清单。

        必须在用户点选完模板与创建方式之后调用；随后用 fill_lesson_plan_fields 分批填写各栏目。
        """
        guard = order_guard() or template_guard(template_id)
        if guard:
            return guard
        template = await session.get(Template, template_id)
        if template is None:
            return "错误：模板不存在。请先调用 list_lesson_templates 获取正确的模板编号。"
        if template.user_id != ctx.user_id and not (template.is_public or template.is_official):
            return "错误：无权使用该模板。"
        if template.user_id != ctx.user_id:
            return (
                f"错误：「{template.name}」是公有模板，不能直接用它建教案。"
                f"请先调用 copy_template(template_id={template.id}) 把它拷进用户自己的模板库，"
                "再用拷贝返回的新编号继续。"
            )
        raw_fields = (template.content or {}).get("fields") or []
        groups = FieldGroups.from_template(raw_fields)
        fillable = [
            str(item["id"])
            for item in raw_fields
            if isinstance(item, dict) and item.get("id") and item.get("type") not in ("label", "divider")
        ]
        if not fillable:
            return "错误：该模板没有可填写的栏目，请让用户换一个模板。"

        plan = LessonPlan(
            title=(title or "未命名教案").strip()[:200],
            subject=(subject or "").strip()[:20] or None,
            grade=(grade or "").strip()[:20] or None,
            template_id=template.id,
            # 建教案当时把模板结构快照进教案：模板之后被改或被删，已经写好的教案不能跟着变样
            content={"fields": raw_fields, "values": {}},
            is_public=False,
            user_id=ctx.user_id,
            creator_name=ctx.username,
        )
        session.add(plan)
        await session.flush()
        logger.info("[Agent] 新建教案 %s（模板 %s，%s 栏）", plan.id, template.name, len(fillable))
        flow.clear()
        rules = group_rules(groups, "fill_lesson_plan_fields") if groups.segmented else ""
        return (
            f"教案《{plan.title}》已创建（内部编号 {plan.id}），使用模板「{template.name}」。"
            f"待填栏目共 {len(fillable)} 个：\n{groups.lines(fillable)}\n\n{rules}"
            f"请按上方清单依次调用 fill_lesson_plan_fields 生成内容（lesson_plan_id 一律传 {plan.id}），"
            "一轮回复并列发 2~3 个调用、等这批返回后再发下一批，直到每一栏都填过；"
            "每栏内容只写正文，不要重复栏目名。"
            f'全部填完后调用 export_lesson_plan(lesson_plan_id={plan.id}, format="docx") 生成 Word 文件。'
        )

    @tool
    async def fill_lesson_plan_fields(
        lesson_plan_id: Annotated[int, Field(description="教案编号，来自 create_lesson_plan 的返回结果")],
        fields: Annotated[dict[str, str], Field(description="栏目内容，key 为栏目 id、value 为正文")],
    ) -> str:
        """为刚创建的教案批量写入栏目内容（直接落库）。

        key 必须是 create_lesson_plan 返回清单里的栏目 id。一轮填 2~3 栏，直到所有栏目都填完。
        """
        plan = await session.get(LessonPlan, lesson_plan_id)
        if plan is None:
            return "错误：教案不存在。请确认 lesson_plan_id 来自 create_lesson_plan 的返回结果。"
        if plan.user_id != ctx.user_id:
            return "错误：无权修改该教案。"

        raw_fields = (plan.content or {}).get("fields") or []
        fillable = {
            str(item["id"])
            for item in raw_fields
            if isinstance(item, dict) and item.get("id") and item.get("type") not in ("label", "divider")
        }
        values = dict((plan.content or {}).get("values") or {})
        skipped: list[str] = []
        filled = 0
        for key, value in normalize_fields(fields).items():
            text = str(value)
            if str(key) not in fillable:
                skipped.append(str(key))
                continue
            if text.strip():
                values[str(key)] = text
                filled += 1
        # JSONB 列要靠整份赋值才会被标记为已修改，原地改字典不会被 UPDATE 出去
        plan.content = {"fields": raw_fields, "values": values}
        await session.flush()

        remaining = [key for key in fillable if not values.get(key, "").strip()]
        ignored = f"，忽略模板中不存在的栏目：{'、'.join(skipped)}" if skipped else ""
        tail = (
            f"还剩 {len(remaining)} 栏未填：{'、'.join(remaining)}。请继续调用 fill_lesson_plan_fields 补齐。"
            if remaining
            else "所有栏目已填完，请调用 export_lesson_plan 生成 Word 文件。"
        )
        return f"本次已填写 {filled} 个栏目{ignored}。{tail}"

    @tool
    async def open_lesson_plan_create(
        template_id: Annotated[int, Field(description="模板编号，必须与用户刚点选的那个一致")],
        topic: Annotated[str | None, Field(description="课题名称，已知就务必传入，页面会自动填入")] = None,
        subject: Annotated[str | None, Field(description="学科，可选；没把握就留空，交给页面回填")] = None,
        grade: Annotated[str | None, Field(description="年级，可选；没把握就留空，交给页面回填")] = None,
    ) -> str:
        """让前端跳转到教案新增页，预选好模板并填入课题（表单路线的落地）。

        用户选择「去教案新增页创建」时调用；跳转后用户在表单页继续对话，由 fill_form_field 逐栏代填。
        """
        guard = order_guard() or template_guard(template_id)
        if guard:
            return guard
        template = await session.get(Template, template_id)
        if template is None:
            return "错误：模板不存在。请先调用 list_lesson_templates 获取正确的模板编号。"
        if template.user_id != ctx.user_id and not (template.is_public or template.is_official):
            return "错误：无权使用该模板。"
        if template.user_id != ctx.user_id:
            return (
                f"错误：「{template.name}」是公有模板，不能直接用它打开新增页。"
                f"请先调用 copy_template(template_id={template.id}) 把它拷进用户自己的模板库，"
                "再用拷贝返回的新编号继续。"
            )

        basic: dict[str, str] = {}
        if topic:
            basic["topic"] = topic.strip()[:60]
        if subject:
            basic["subject"] = subject.strip()[:20]
        if grade:
            basic["grade"] = grade.strip()[:20]
        url = f"/lesson-plans/create?template_id={template.id}"
        emit({"type": "tool", "tool_type": "navigate", "url": url, "basic": basic})
        flow.clear()
        # 页面会按课题自动回填学科、年级、单元，所以这三个里只有课题值得带过去
        filled = f"，课题「{basic['topic']}」已自动填入" if basic.get("topic") else ""
        return (
            f"已通知前端跳转到教案新增页：模板「{template.name}」已选定{filled}。"
            "请告知用户：页面已打开、基础信息已自动填好，请核对确认，确认后让你开始生成整篇教案。"
            "本轮到此收尾，不要再调用其他工具。"
        )

    return [
        list_lesson_templates,
        copy_template,
        create_lesson_plan,
        fill_lesson_plan_fields,
        open_lesson_plan_create,
    ]


# ── 选择卡片 ──

# 问的先后由服务端排：模板 → 创建方式 → 其他。模型把两问摆倒了不影响用户看到的流程
_KIND_ORDER = ("template", "method", "other")


def _choice_tool(request: AgentChatInput, ctx: ToolContext, emit: Emit) -> list[BaseTool]:
    in_form_mode = bool(request.available_fields)
    flow = ctx.plan_flow

    @tool
    async def ask_choice(
        questions: Annotated[
            list[ChoiceQuestion],
            Field(
                min_length=1,
                max_length=3,
                description="一次要用户回答的问题；新建教案必须一次问完「选模板」和「选创建方式」两问",
            ),
        ],
    ) -> str:
        """向用户弹出可点击的选项卡片，替代要用户打字回复的纯文本提问。

        一次调用可带 1~3 问，前端逐问显示，用户点完最后一问才把所有选择作为一条消息发回。
        新建教案时务必把「选模板」和「选创建方式」放进同一次调用，严禁分两轮问。
        本工具必须单独调用：同一次回复里不许再发其他工具，也不许在正文里重复罗列选项。
        """
        cleaned: list[dict[str, Any]] = []
        for item in questions:
            options = [
                {"label": option.label.strip(), "description": (option.description or "").strip()}
                for option in item.options[:MAX_CHOICE_OPTIONS]
                if option.label.strip()
            ]
            if item.question.strip() and options:
                cleaned.append({"kind": item.kind, "question": item.question.strip(), "options": options})
        cleaned = cleaned[:MAX_CHOICE_QUESTIONS]
        if not cleaned:
            return "错误：每一问都要有 question，且至少带一个写了 label 的选项。"

        # 表单模式没有"选模板/选创建方式"这一说：模板已由页面选定，生成就是往当前表单里填。
        # 模型被新建流程的惯性带偏时在这里拦下——工具层的约束比提示词里多写一句可靠。
        if in_form_mode and any(item["kind"] != "other" for item in cleaned):
            return (
                "错误：用户当前已在教案表单页，模板已加载、创建方式已定，不存在选模板或选创建方式。"
                "用户要生成教案就直接按字段清单调用 fill_form_field 逐栏填充当前表单。"
            )

        known = flow.templates
        template_question = next((item for item in cleaned if item["kind"] == "template"), None)
        if template_question is not None and not known:
            return "错误：请先调用 list_lesson_templates 拿清单，再把清单里的模板列为选项，严禁凭记忆罗列。"
        # label 就是下一轮对号的钥匙：模型加了后缀或改了字，用户点选回来解析不出编号，
        # 卡片弹了却什么也推进不了。与其让它在建档那步静默打回，这里直接报出对不上的名字
        if template_question is not None:
            names = [item.name for item in known]
            chosen_labels = [option["label"] for option in template_question["options"]]
            off_list = [label for label in chosen_labels if label not in names]
            if off_list:
                return (
                    f"错误：模板选项里有清单之外的名字：{'、'.join(off_list)}。"
                    "label 必须逐字照抄 list_lesson_templates 清单里的模板名（系统按 label 对号解析编号），"
                    f"栏目数和「公有模板」写进 description，可用清单：{'、'.join(names)}。"
                )

        method_question = next((item for item in cleaned if item["kind"] == "method"), None)
        if method_question is not None:
            # 判据是"模板有没有被真正定下来"，不是进度本身：卡片一弹进度就推进了，
            # 用户回的消息若对不上号（打字打错、或答的是别的事），模板其实还没定。
            # 这时若按进度拦，模型想重问方式会被永久打回——卡片弹过一次就成了死路
            if not known:
                return (
                    "错误：新建教案必须先确定模板，才能问创建方式。请先调用 list_lesson_templates，"
                    "再把模板问和创建方式问放进同一次 ask_choice。"
                )
            if flow.template is None and template_question is None:
                return (
                    "错误：模板还没定，不能只问创建方式。请把「选模板」和「选创建方式」合并为一次 ask_choice"
                    "（questions 传两项，kind 分别传 template、method）。"
                )

        cleaned.sort(key=lambda item: _KIND_ORDER.index(item["kind"]))
        emit(
            {
                "type": "choice",
                "questions": [{"question": item["question"], "options": item["options"]} for item in cleaned],
            }
        )
        if any(item["kind"] == "template" for item in cleaned):
            flow.set_step("template_picked")
        if method_question is not None:
            flow.note_method_options([option["label"] for option in method_question["options"]])
            flow.set_step("method_picked")
        return (
            f"选择卡片已展示给用户（共 {len(cleaned)} 问）。本轮到此收尾：不要再调用其他工具，"
            "也不要在正文里重复罗列选项。用户逐问点选后，所选文字会按问的顺序作为一条消息发回给你。"
        )

    return [ask_choice]


# ── 外部参考：课件风格、联网检索 ──


def _reference_tools(request: AgentChatInput, ctx: ToolContext, emit: Emit) -> list[BaseTool]:
    @tool
    async def search_ppt_reference(
        topic: Annotated[str, Field(description="教案课题/标题")],
        subject: Annotated[str | None, Field(description="学科，可选")] = None,
        grade: Annotated[str | None, Field(description="年级，可选")] = None,
    ) -> str:
        """搜索与课题相关的课件参考资料（只给标题、链接、摘要），用来感知这一类课件的常见风格。

        可选步骤：搜不到或失败就直接继续按内置主题生成，不要反复重试。
        不下载、不解析外部文件，只看文字。
        """
        query = " ".join(part for part in (topic, subject, grade, "PPT 课件") if part)
        hits = await search_web_references(query)
        if not hits:
            return (
                "未找到相关参考资料（搜索服务不可用或无结果）。"
                "请跳过参考步骤，直接按系统提示词中的主题指南选择内置主题生成。"
            )
        return (
            f"找到 {len(hits)} 条与「{topic}」相关的 PPT 参考资料：\n{_web_lines(hits, 100)}\n\n"
            "请从文件名与摘要中感知该类课件的常见风格（学科特点、学段、配色倾向），然后：\n"
            "1. 按系统提示词的主题指南选择最匹配的内置主题，在 generate_ppt_slides 的 theme 参数中指定\n"
            "2. 页面内容仍以教案原文为准，严禁照抄参考资料中的文字"
        )

    tools: list[BaseTool] = [search_ppt_reference]
    # 联网工具只在用户本轮打开「联网」开关时注册：默认助手只用站内教案、模板和资料库
    if request.web_search:

        @tool
        async def search_web(
            query: Annotated[str, Field(description="搜索关键词，中文，尽量具体（课题 + 学科 + 年级）")],
        ) -> str:
            """搜索公开网页资料，返回标题、链接、摘要。

            仅在本轮用户开启了「联网」且确实需要教案、资料库之外的外部信息时使用；搜不到就继续，不要重试。
            """
            hits = await search_web_references(query)
            if not hits:
                return "联网搜索没有返回结果（搜索服务不可用或无结果）。请基于站内资料继续，不要反复重试。"
            return (
                f"找到 {len(hits)} 条与「{query}」相关的网页：\n{_web_lines(hits, 200)}\n\n"
                "引用外部信息时说明来源标题；摘要里没有的细节不要臆造。"
                "教案正文仍以教学常识和站内资料为主。"
            )

        tools.append(search_web)
    return tools


def _web_lines(hits: list[WebHit], snippet_limit: int) -> str:
    return "\n".join(
        f"{index}. 《{hit.title}》\n   链接: {hit.url}\n   摘要: {(hit.snippet or '无')[:snippet_limit]}"
        for index, hit in enumerate(hits, 1)
    )


# ── 课件生成：先大纲，经确认后再逐页 ──


def _json_of(model: BaseModel) -> dict[str, Any]:
    """工具入参模型 → 落库与推流用的纯 dict：丢掉没填的项，键名保持 snake_case。

    JSONB 列里只放原生 JSON 值，Pydantic 模型对象塞进去会在写库时才报错，
    那时用户已经看到了"生成失败"，而真正的原因只在服务端日志里。
    """
    return {key: value for key, value in model.model_dump(mode="json").items() if value is not None}


def _ppt_tools(request: AgentChatInput, ctx: ToolContext, emit: Emit) -> list[BaseTool]:
    session = ctx.session

    @tool
    async def create_ppt_outline(
        outline: Annotated[
            list[OutlinePage],
            Field(
                description=f"大纲：第 1 页为封面，最后一页为结束页，中间为内容页；页数按教案实际篇幅定"
                f"（一般 10~20 页），最多 {MAX_PPT_OUTLINE_SECTIONS} 页，为不同内容搭配不同版式"
            ),
        ],
        lesson_plan_id: Annotated[
            int | None, Field(description="教案编号，根据教案生成时必传（来自 search_lesson_plans 的结果）")
        ] = None,
        ppt_id: Annotated[
            int | None, Field(description="已有课件编号；重新规划大纲时传入，省略则新建一条记录")
        ] = None,
        title: Annotated[str | None, Field(description="课件标题，可选；缺省取教案标题")] = None,
        stage: Annotated[
            Literal["primary", "middle", "college"] | None,
            Field(description="学段：primary 小学 / middle 中学 / college 大学（含高职）"),
        ] = None,
    ) -> str:
        """规划课件大纲并落库，这是生成 PPT 的第一步。

        用户要求生成 PPT、幻灯片、课件时必须先调用本工具，严禁跳过它直接生成页面。
        大纲写入记录后系统会请用户确认，确认完的这一轮才逐页生成。
        """
        plan_title = subject = grade = ""
        if lesson_plan_id:
            plan = await session.get(LessonPlan, lesson_plan_id)
            if plan is None:
                return "错误：教案不存在。请先调用 search_lesson_plans 搜索正确的教案。"
            denied = _access_error(plan.user_id, plan.is_public, ctx.user_id, "教案")
            if denied:
                return f"错误：{denied}"
            plan_title, subject, grade = plan.title or "", plan.subject or "", plan.grade or ""

        sections: list[dict[str, Any]] = []
        for page in outline:
            heading = page.title.strip()
            if not heading:
                continue
            sections.append(
                {
                    "title": heading,
                    "points": [text for text in (item.strip() for item in (page.points or [])) if text],
                    # 版式只作为建议保留：认不出的一律清空，让生成页面那一步按内容重新选
                    "layout": page.layout if page.layout in SLIDE_LAYOUTS else None,
                }
            )
        if not sections:
            return "错误：大纲为空。请为 outline 的每一页至少写出 title。"
        if len(sections) > MAX_PPT_OUTLINE_SECTIONS:
            return f"错误：大纲共 {len(sections)} 页，超过 {MAX_PPT_OUTLINE_SECTIONS} 页上限，请精简后重试。"

        final_title = (title or plan_title or "未命名课件").strip()
        if ppt_id:
            record = await session.get(Presentation, ppt_id)
            if record is None or record.user_id != ctx.user_id:
                return "错误：课件不存在或无权操作。请省略 ppt_id 参数，让系统创建新记录。"
            previous = record.doc if isinstance(record.doc, dict) else {}
            record.outline = {"sections": sections}
            record.status = "outline"
            record.title = final_title
            if lesson_plan_id:
                record.lesson_plan_id = lesson_plan_id
            # 整份重新赋值：原地改 JSONB 里的键，SQLAlchemy 不会把这一列标成已修改
            record.doc = {
                **previous,
                "title": final_title,
                "theme": previous.get("theme") or {},
                # 重置已生成页面：新大纲可能比旧的短，不清掉会残留上次的页面
                "slides": [],
                "stage": stage or previous.get("stage") or "middle",
            }
        else:
            record = Presentation(
                title=final_title,
                subject=subject or None,
                grade=grade or None,
                lesson_plan_id=lesson_plan_id,
                outline={"sections": sections},
                status="outline",
                doc={"title": final_title, "theme": {}, "slides": [], "stage": stage or "middle"},
                is_public=False,
                user_id=ctx.user_id,
                creator_name=ctx.username,
            )
            session.add(record)
        await session.flush()
        logger.info("[Agent] 课件大纲 %s，共 %s 页", record.id, len(sections))

        listing = "，".join(f"{index}.{item['title']}" for index, item in enumerate(sections, 1))
        return (
            f"课件记录已就绪（内部编号 {record.id}），大纲共 {len(sections)} 页：{listing}。"
            "请继续调用 generate_ppt_slides，从第 1 页开始分批连续生成至全部完成，无需再次请求用户确认。"
        )

    @tool
    async def generate_ppt_slides(
        ppt_id: Annotated[int, Field(description="课件编号，来自 create_ppt_outline 的返回结果")],
        slides: Annotated[
            list[SlideSpec],
            Field(
                description="与页码范围逐页对应的幻灯片内容；为不同内容选用不同版式，不要每页都是标题加要点"
            ),
        ],
        from_page: Annotated[
            int | None, Field(description="起始页码（从 1 开始，含），缺省接着已生成的下一页")
        ] = None,
        to_page: Annotated[int | None, Field(description="结束页码（含），缺省为大纲最后一页")] = None,
        theme: Annotated[
            ThemeSpec | None, Field(description="主题色板，可选；第一批务必传入，后续批次只在用户改配色时传")
        ] = None,
    ) -> str:
        """按大纲生成一个页码区间的幻灯片内容，每页实时推给前端预览。

        必须在 create_ppt_outline 之后调用，按 from_page/to_page 分批连续生成（每批 3~4 页），不要跳过页码。
        """
        record = await session.get(Presentation, ppt_id)
        if record is None or record.user_id != ctx.user_id:
            return "错误：课件不存在或无权操作。请先调用 create_ppt_outline 创建记录。"
        sections = (record.outline or {}).get("sections") or []
        if not sections:
            return "错误：该课件还没有大纲。请先调用 create_ppt_outline。"

        total = len(sections)
        doc = record.doc if isinstance(record.doc, dict) else {}
        existing = doc.get("slides") or []
        start = max(1, from_page or len(existing) + 1)
        end = min(total, to_page or total)
        if start > end:
            return f"错误：页码范围无效（第 {start} 页至第 {end} 页）。"
        # 顺序约束：允许覆盖重写已经生成过的页，但不许跳页——跳过去会在页面数组里留下空洞，
        # 导出时那一页只剩背景，用户在编辑器里看到的就是一个空白页
        if start > len(existing) + 1:
            return f"错误：必须按顺序生成。当前已生成 {len(existing)} 页，请从第 {len(existing) + 1} 页开始。"
        expected = end - start + 1
        if len(slides) != expected:
            return f"错误：slides 传了 {len(slides)} 页，页码范围 {start}-{end} 需要 {expected} 页。"

        normalized: list[dict[str, Any]] = []
        for offset, spec in enumerate(slides):
            page_number = start - 1 + offset
            payload = _json_of(spec)
            # 标题缺省取大纲里这一页的标题：模型分批时常常只写要点，漏掉的标题不该让页面空着
            payload["title"] = (payload.get("title") or "").strip() or sections[page_number]["title"]
            if page_number == 0:
                payload["layout"] = "cover"
            normalized.append(_json_of(normalize_slide(payload)))

        doc["title"] = doc.get("title") or record.title
        doc["slides"] = [*existing[: start - 1], *normalized, *existing[end:]]
        if theme is not None:
            doc["theme"] = {**(doc.get("theme") or {}), **_json_of(theme)}
        doc.setdefault("stage", "middle")
        record.doc = doc
        record.status = "done" if end >= total else "outline"
        await session.flush()

        # 业务事件：逐页推送，前端实时预览
        for offset, slide in enumerate(normalized):
            emit({"type": "pptSlide", "ppt_id": record.id, "index": start - 1 + offset, "slide": slide})
        if record.status == "done":
            emit({"type": "pptDone", "ppt_id": record.id, "slide_count": len(doc["slides"])})

        remaining = total - end
        tail = f"，还剩 {remaining} 页，请继续从第 {end + 1} 页生成" if remaining else "，已全部完成"
        return f"已生成第 {start}-{end} 页（共 {total} 页{tail}）。"

    @tool
    async def update_ppt_slide(
        page: Annotated[int, Field(description="要修改的页码（从 1 开始）")],
        ppt_id: Annotated[
            int | None, Field(description="课件编号；用户正在编辑时可省略，系统会自动使用当前编辑的那份")
        ] = None,
        title: Annotated[str | None, Field(description="新的标题")] = None,
        bullets: Annotated[list[str] | None, Field(description="新的要点列表（整体替换）")] = None,
        content: Annotated[str | None, Field(description="新的正文（整体替换）")] = None,
        notes: Annotated[str | None, Field(description="新的演讲者备注")] = None,
        layout: Annotated[str | None, Field(description=f"新的版式：{LAYOUT_LIST_HINT}")] = None,
        big_number: Annotated[BigNumberSpec | None, Field(description="大数字（layout=big-number）")] = None,
        chart: Annotated[ChartSpec | None, Field(description="图表数据（layout=chart）")] = None,
        timeline: Annotated[
            list[TimelineStepSpec] | None, Field(description="步骤（layout=timeline）")
        ] = None,
        compare: Annotated[CompareSpec | None, Field(description="左右两栏（layout=compare）")] = None,
        activity: Annotated[ActivitySpec | None, Field(description="活动（layout=activity）")] = None,
    ) -> str:
        """修改已生成课件的某一页（标题、要点、正文、备注或版式），改完推送事件刷新前端画布。

        用户正在编辑课件并要求调整某一页时使用。页码从 1 开始；换版式时同步提供该版式需要的富元素。
        """
        target_id = ppt_id if ppt_id else request.ppt_id
        if not target_id:
            return "错误：缺少课件编号。请确认用户正在编辑某份课件，或在参数中传入 ppt_id。"
        record = await session.get(Presentation, target_id)
        if record is None or record.user_id != ctx.user_id:
            return "错误：课件不存在或无权操作。"
        doc = record.doc if isinstance(record.doc, dict) else {}
        slides = doc.get("slides") or []
        index = max(0, page - 1)
        if index >= len(slides):
            return f"错误：页码 {page} 超出范围，该课件共 {len(slides)} 页。"

        # 只覆盖传了的那几项：这些参数缺省就是"这一项不动"，而不是"把它清空"
        patch: dict[str, Any] = {}
        for key, value in (
            ("title", title),
            ("bullets", bullets),
            ("content", content),
            ("notes", notes),
            ("layout", layout),
            ("big_number", big_number),
            ("chart", chart),
            ("timeline", timeline),
            ("compare", compare),
            ("activity", activity),
        ):
            if value is None:
                continue
            if isinstance(value, BaseModel):
                patch[key] = _json_of(value)
            elif isinstance(value, list):
                patch[key] = [_json_of(item) if isinstance(item, BaseModel) else item for item in value]
            else:
                patch[key] = value
        if not patch:
            return (
                "错误：没有需要修改的内容，请至少提供 title / bullets / content / notes / layout /"
                " big_number / chart / timeline / compare / activity 之一。"
            )

        updated = _json_of(normalize_slide({**(slides[index] or {}), **patch}))
        doc["slides"] = [*slides[:index], updated, *slides[index + 1 :]]
        record.doc = doc
        await session.flush()
        emit({"type": "pptSlide", "ppt_id": record.id, "index": index, "slide": updated})
        return f"第 {index + 1} 页已更新。"

    return [create_ppt_outline, generate_ppt_slides, update_ppt_slide]


# ── 装配 ──


def build_agent_tools(request: AgentChatInput, ctx: ToolContext, emit: Emit) -> list[BaseTool]:
    """按本轮上下文拼出这一轮能用的工具。

    分组的准入条件与 `prompt.build_system_prompt` 的片段开关同源：注册了却没在提示词里讲规则的工具，
    模型会拿着它乱用；讲了却没注册的工具，模型会凭空尝试，然后卡在一句"该工具不存在"上。
    """
    in_form_mode = bool(request.available_fields)
    tools: list[BaseTool] = []
    if in_form_mode:
        tools += _form_tools(request, ctx, emit)
    tools += _search_tools(request, ctx, emit)
    tools += _export_tool(request, ctx, emit)
    if not in_form_mode:
        tools += _new_plan_tools(request, ctx, emit)
    tools += _choice_tool(request, ctx, emit)
    tools += _reference_tools(request, ctx, emit)
    tools += _ppt_tools(request, ctx, emit)
    return tools
