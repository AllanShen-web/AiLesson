"""一轮 Agent 对话：把消息跑成一张图，过程事件逐条推给浏览器。

图的形状是 `agent → tools → agent`，收尾前多一道 `review`。模型说"我答完了"不等于真答完了：
上一条回复可能被输出长度上限截断（那批工具调用是残缺 JSON，一个字段都没真正填进去），
整篇代填也可能还剩空栏。这两件事在图上核对一次，比在提示词里祈祷模型自觉可靠——
"忘记复查"和"忘记第二课时"本来就是同一类失效。

上下文预算是这一层的主要工作。历史在每一步都会全量重发（agent 循环自身无状态），
所以预算数字直接决定单轮生成一篇教案的输入量；装不下的部分压成一段摘要塞回系统提示词，
而不是整段丢掉——丢了的那一段，模型连刚才在说什么都抓不住。

会话生命周期与接口层不同：SSE 响应一旦开始，请求级会话就随依赖退出被关掉了，
所以本轮自己开一个会话，并在每批工具跑完时落一次盘。客户端中途断开或某一步报错，
都不该把已经生成好的内容一起丢掉。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, TypedDict, cast

import tiktoken
from fastapi import BackgroundTasks, Depends
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.tracers import LangChainTracer
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ailan.ai_chat.agent import policy
from ailan.ai_chat.agent.plan_flow import PlanFlow, PlanFlowStore
from ailan.ai_chat.agent.prompt import (
    FieldGroups,
    build_system_prompt,
    review_reminder,
    split_suggestions,
    suggestion_candidates,
    truncation_reminder,
)
from ailan.ai_chat.agent.tools import ToolContext, build_agent_tools
from ailan.ai_chat.attachments import build_user_message
from ailan.ai_chat.config_service import AssistantConfigService
from ailan.ai_chat.models import ConfirmationRequest, Conversation, Message
from ailan.ai_chat.schemas import AgentChatInput
from ailan.ai_chat.service import (
    attachment_dicts,
    build_history_messages,
    load_history,
    resolve_conversation,
    save_message,
)
from ailan.ai_chat.sse import Emit, SSECallbackHandler
from ailan.core.config import settings
from ailan.core.db import SessionFactory
from ailan.core.deps import CurrentUser
from ailan.lesson_plans.models import LessonPlan
from ailan.llm.factory import ModelSpec, build_backup_models, build_model, is_tracing_enabled
from ailan.rag.schemas import RagHit
from ailan.rag.service import RagService
from ailan.storage.service import StorageDep, StorageService

logger = logging.getLogger(__name__)

# 整篇代填就是把「填单字段」这件事循环调用几十次（28 栏的两课时模板一轮要 40+ 次，
# 还有读取与导出等开销），15 会把大教案生成到一半掐断。这里取最大模板字段数再留一倍冗余，
# 真正防跑飞的兜底是图上的超步上限（按本值折算）。
MAX_AGENT_STEPS = 80
# 每步请求体的 token 预算。设成模型窗口上限（128k）是错的：顶格用会把整轮输入量乘到四五倍，
# 等待时间同比例涨。留一半余量给正文增长。
CONTEXT_TOKEN_BUDGET = 64_000
# 工具定义随每次请求发出，但不在 messages 里、裁不到，按实测预留（表单模式十几个工具约 1.1 万字符）
TOOL_SCHEMA_TOKEN_RESERVE = 4_500
# 历史被切掉时要压一段摘要塞进系统提示词，摘要本身也占额度
SUMMARY_TOKEN_RESERVE = 2_000
# 装不下时至少保留最近几条原文：全压成摘要，模型连刚才在说什么都接不上
HISTORY_MIN_KEEP = 2
# 回捞条数上限：真正的闸门是 token，这里只是防止把整张消息表读进内存
HISTORY_SCAN_LIMIT = 300
# 预算紧到不够装历史时，也给它留这点底线，宁可这一步超一点
MIN_HISTORY_BUDGET = 4_000
# 喂给摘要模型的原文上限。回捞放宽到 300 条后「被切掉的那批」可能有一百多条，
# 全塞进去会把摘要这一步自己顶穿窗口（失败是静默返回空串，等于整段记忆没了）。从新往旧取。
SUMMARY_INPUT_CHAR_LIMIT = 20_000
SUMMARY_LINE_CHAR_LIMIT = 500
# 每轮无条件查几条资料库片段
RAG_PRESEARCH_TOP_K = 3

# 整篇代填的判据：本轮至少填过这么多栏才复查，单字段生成与纯问答不该被逼着补完整篇
WHOLE_PLAN_MIN_FILLS = 2
# 复查最多拦回几轮。缺栏有两种成因：模型真忘了（同名字段按名字核对会把第二课时当成已填），
# 或用户明说某栏不填。后者只能由模型自己声明，所以留两轮：第一轮逼它补或说明，仍缺再拦一次，第三次放行。
MAX_AGENT_REVIEWS = 2
# 输出被上限截断时最多打回几次。截断那轮的 tool_calls 是残缺 JSON，正文也是空——
# 不识别就会变成「什么都没做但已经回答完了」，所以单独计数、单独提醒（把批量压到一次一两栏）。
MAX_LENGTH_CUTS = 3
# 最终回答按原样切块回放：整段一次推出会跳过前端的打字机效果
FINAL_CHUNK_CHARS = 24
# 落库前把思考文案压成一行，折叠面板一行摆不下整段推理
STEP_LABEL_CHARS = 40

ASK_CHOICE_TOOL = "ask_choice"
FILL_FORM_TOOL = "fill_form_field"
OUTLINE_TOOL = "create_ppt_outline"

CHOICE_CANCELLED = (
    "该调用已作废：本轮已向用户弹出选项卡片，用户点选前不得执行任何动作，"
    "一轮也只允许一张卡片（一张卡片可以带多个问题）。请用一句话收尾，提醒用户在卡片中点选。"
)
AWAITING_CONFIRMATION = "操作已暂停，等待用户在确认卡片中批准后继续。"

# 工具名 → 折叠面板上的步骤文案，`{field}` 由本轮字段名填入。
# 键是工具名，按 Python 的写法保持 snake_case；`kind` 却是前端拿去选图标的枚举值，
# 这些步骤字典整块存进 JSONB、读出来原样返回（不经过 SSE 那一层改名），所以它写 camelCase。
AGENT_STEP_META: dict[str, tuple[str, str]] = {
    FILL_FORM_TOOL: ("fillField", "正在生成【{field}】"),
    "search_lesson_plans": ("searchPlans", "正在搜索教案"),
    "read_lesson_plan": ("readPlan", "正在读取教案内容"),
    "search_knowledge_base": ("searchFiles", "正在搜索资料库"),
    "read_knowledge_file": ("readFile", "正在读取文件内容"),
    "search_knowledge_chunks": ("ragSearch", "正在检索资料库片段"),
    "search_web": ("searchFiles", "正在联网搜索"),
    "export_lesson_plan": ("export", "正在导出文件"),
    "list_lesson_templates": ("searchPlans", "正在查看可用模板"),
    "copy_template": ("generic", "正在拷贝模板"),
    ASK_CHOICE_TOOL: ("generic", "等待你选择"),
    "create_lesson_plan": ("fill_field", "正在创建教案记录"),
    "fill_lesson_plan_fields": ("fill_field", "正在生成教案内容"),
    "open_lesson_plan_create": ("generic", "正在打开教案新增页"),
    OUTLINE_TOOL: ("ppt_outline", "正在规划课件大纲"),
    "generate_ppt_slides": ("ppt_slide", "正在生成课件页面"),
    "update_ppt_slide": ("ppt_slide", "正在修改课件页面"),
    "search_ppt_reference": ("search_plans", "正在搜索课件参考资料"),
}

# 进程内共享：checkpoint 只在本轮活着、用完即删，流程登记表却要跨轮记进度。
# 换持久化实现（数据库存档）时只动这两行，图与节点不认识它们的存在。
_CHECKPOINT = InMemorySaver()
_PLAN_FLOWS = PlanFlowStore()


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    """词表首次加载要几百毫秒，进程内只建一次。

    cl100k 对中文比模型实际分词更费 token，按它计数只会偏保守，不会顶穿窗口。
    """
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def count_message_tokens(text: str) -> int:
    """一条消息进请求体还要带角色与分隔符，按 4 枚计。"""
    return count_tokens(text) + 4


def split_history_by_budget(
    history: list[Message], budget_tokens: int
) -> tuple[list[Message], list[Message]]:
    """按 token 预算切历史：从最新一条往回装，装不下的切给摘要。

    超预算仍强制保留最近 `HISTORY_MIN_KEEP` 条——宁可这一步多花点，也不能让模型失忆到接不上话。
    返回 `(更早的, 保留的)`，两截都保持时间正序。
    """
    used = 0
    keep_from = len(history)
    for index in range(len(history) - 1, -1, -1):
        tokens = count_message_tokens(str(history[index].content or ""))
        if used + tokens > budget_tokens and len(history) - index > HISTORY_MIN_KEEP:
            break
        used += tokens
        keep_from = index
    return history[:keep_from], history[keep_from:]


def _merge_messages(left: list[BaseMessage], right: list[BaseMessage]) -> list[BaseMessage]:
    return [*left, *right]


def _accumulate(left: int, right: int) -> int:
    return left + right


def _sticky(left: bool, right: bool) -> bool:
    """本轮只要挂起过一条待确认的工具调用，就一直成立到收尾。"""
    return left or right


class AgentState(TypedDict):
    """图状态：消息流 + 步数（防跑飞）+ 收尾闸门用的几个计数。"""

    messages: Annotated[list[BaseMessage], _merge_messages]
    step: Annotated[int, _accumulate]
    # 有需要审批的工具被挂起时置真，路由据此强制收尾，把确认请求交给用户，绝不擅自执行
    await_confirmation: Annotated[bool, _sticky]
    review_count: int
    length_cut_count: int
    # review 的裁决：真 = 带剩余字段清单退回 agent 继续补栏，假 = 放行结束
    review_intercept: bool


class ConversationSummary(BaseModel):
    """历史压缩的结果：只要一句话，但用结构化输出强制模型必须给。"""

    summary: str = Field(description="对话摘要")


def _text_of(message: BaseMessage) -> str:
    """多模态消息的 content 是分段数组，这一层只关心纯文本。"""
    return message.content if isinstance(message.content, str) else ""


def _field_label(field: Any) -> str:
    """栏目的人类可读名：两课时模板同名栏目靠分组名区分，没有分组就退回纯 label。"""
    if field is None:
        return "字段"
    return f"{field.group}·{field.label}" if field.group else field.label


def _step_meta(tool_name: str, field_name: str) -> tuple[str, str]:
    kind, label = AGENT_STEP_META.get(tool_name, ("generic", "正在处理"))
    return kind, label.replace("{field}", field_name)


def _chunks(text: str, size: int) -> list[str]:
    return [text[start : start + size] for start in range(0, len(text), size)]


def _tool_error_feedback(tool_name: str, error: BaseException) -> str:
    """工具调用抛错时回填给模型的说明。

    参数没过校验必须把「哪一项、为什么不行」带回去：只回一句「工具执行出错，请稍后再试」，
    模型看不到原因就会一字不改地重发同一个调用，实测连错三次后直接放弃、整条流程静默卡死。
    """
    if isinstance(error, ValidationError):
        detail = "\n".join(
            f"- {'.'.join(str(part) for part in item['loc']) or '参数'}: {item['msg']}"
            for item in error.errors()[:5]
        )
    else:
        detail = str(error)[:400] or "未知错误"
    return (
        f"工具 {tool_name} 本次调用没有执行，原因：\n{detail}\n"
        "请改正后重新调用这一工具（只改正参数，不要重新问用户，也不要把同一个调用原样发第二遍）。"
    )


def _bind_tools_with_fallback(
    primary: BaseChatModel, backups: list[BaseChatModel], tools: list[BaseTool]
) -> Runnable:
    """绑定顺序要紧：绑工具 → 同模型重试 → 换模型兜底。

    `with_fallbacks` 返回的可调用对象不再支持 `bind_tools`，工具必须先各绑一次；
    反过来先兜底再绑，换过去的那个模型手里是没有工具的。
    """
    attempts = settings.llm_retry_attempts

    def prepared(model: BaseChatModel) -> Runnable:
        return model.bind_tools(tools).with_retry(stop_after_attempt=attempts)

    return prepared(primary).with_fallbacks([prepared(model) for model in backups])


def _route_after_agent(state: AgentState) -> str:
    """有工具调用且没超步数就执行；其余（收尾 / 超步 / 等确认）一律先过 review 闸门。

    条件边自己不能改状态，所以「等确认时的强制收尾」由 review 节点判。
    """
    last = state["messages"][-1]
    has_tools = bool(getattr(last, "tool_calls", None)) and state["step"] < MAX_AGENT_STEPS
    return "tools" if has_tools else "review"


def _route_after_review(state: AgentState) -> str:
    return "agent" if state["review_intercept"] else END


class AgentTurn:
    """一轮请求的全部状态：上下文组装、图节点、收尾落库都在这个对象上。

    图节点写成方法而不是闭包，是因为本轮要攒的东西不少（产出文件、步骤、表单值、
    弹出过的卡片），摊在 `self` 上比在好几层 `nonlocal` 之间传递好读。
    """

    def __init__(
        self,
        request: AgentChatInput,
        user: CurrentUser,
        session: AsyncSession,
        storage: StorageService,
        emit: Emit,
    ) -> None:
        self.request = request
        self.user = user
        self.session = session
        self.storage = storage
        self._forward = emit

        self.configs = AssistantConfigService(session, storage)
        # Agent 只用向量库的两个只读方法，本轮没有后台任务要挂
        self.rag = RagService(session, self.configs, storage, BackgroundTasks())

        self.groups = FieldGroups.from_request(request.available_fields or [])
        # 表单页带过来的可填栏目：本轮的填充判据、复查清单与步骤文案都看它
        self.fields = request.available_fields or []
        self.primary_model: BaseChatModel
        self.vision_model: BaseChatModel

        # 本轮累积，随助手消息落库
        self.generated_files: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.outline_fallback: dict[str, Any] | None = None
        # 自动保存：本轮的表单值从请求带过来的内容起步
        self.values: dict[str, str] = dict(request.lesson_plan_content or {})
        self._last_saved: dict[str, str] = dict(self.values)

        self.fill_events = 0
        self.choice_asked = False
        self.length_cut_exhausted = False
        self.suggestions: list[str] = []

        self.conversation: Conversation
        self.flow: PlanFlow
        self.tools: list[BaseTool] = []
        self.tool_map: dict[str, BaseTool] = {}
        self.sse_handler: SSECallbackHandler
        self.model_with_tools: Runnable
        self.model_callbacks: list[Any] = []
        self.tool_callbacks: list[Any] = []

    # ── 事件出口 ──

    def emit(self, event: dict[str, Any]) -> None:
        """先记账再转发：工具与模型只认这一条通道，落库要的步骤与文件都在这儿收集。"""
        self._collect(event)
        self._forward(event)

    def _collect(self, event: dict[str, Any]) -> None:
        etype = event.get("type")

        if etype == "file":
            self.generated_files.append(
                {
                    "file_id": event.get("file_id"),
                    "file_name": event.get("file_name"),
                    "format": event.get("format"),
                    "size_bytes": event.get("size_bytes"),
                }
            )
            return

        if etype == "thinking":
            label = " ".join(str(event.get("content") or "").split())[:STEP_LABEL_CHARS]
            if label:
                self.steps.append({"kind": "thinking", "label": label, "pending": False})
            return

        if etype == "toolCall":
            args = event.get("args")
            if not isinstance(args, dict):
                args = {}
            field_id = str(args.get("field_id") or "")
            field = next((item for item in self.fields if item.id == field_id), None)
            kind, label = _step_meta(str(event.get("tool") or ""), _field_label(field))
            # 新步骤开始，上一步置为完成：同一时刻只该有一个「进行中」
            for step in self.steps:
                step["pending"] = False
            self.steps.append({"kind": kind, "label": label, "pending": True})
            return

        if etype == "toolResult":
            last = self.steps[-1] if self.steps else None
            if last is not None and last["pending"]:
                last["pending"] = False
                result = str(event.get("result") or "")
                last["failed"] = result.startswith("错误：") or result.startswith("工具执行出错")
            return

        if etype == "tool" and event.get("tool_type") == FILL_FORM_TOOL:
            if event.get("field_id") and event.get("content"):
                self.values[str(event["field_id"])] = str(event["content"])
                self.fill_events += 1
            return

        if etype == "confirmation" and event.get("tool_name") == OUTLINE_TOOL:
            outline = (event.get("data") or {}).get("outline") or []
            self.outline_fallback = {
                "count": len(outline),
                "titles": "，".join(
                    f"{index + 1}.{page.get('title') if isinstance(page, dict) else ''!s}"
                    for index, page in enumerate(outline)
                ),
            }

    # ── 自动保存 ──

    async def _save_progress(self) -> None:
        """把本轮到目前为止的写入落盘：表单值，以及工具产出的文件与课件行。

        在每批工具跑完时点一次，而不是每填一栏写一次——一轮可能填好几栏，
        逐栏写会把同一个会话踩来踩去；而客户端断开或某一步失败时，已经生成的内容不该跟着丢。
        """
        if self.request.lesson_plan_id and self.values != self._last_saved:
            plan = await self.session.get(LessonPlan, self.request.lesson_plan_id)
            if plan is not None:
                content = plan.content if isinstance(plan.content, dict) else {}
                # JSONB 整块替换：原地改列里的字典，SQLAlchemy 看不出来这一行被改过
                plan.content = {"fields": content.get("fields") or [], "values": dict(self.values)}
                self._last_saved = dict(self.values)
                logger.info("[Agent] 教案 %s 已自动保存 %s 栏", self.request.lesson_plan_id, len(self.values))
        await self.session.commit()

    # ── 图节点 ──

    async def _agent_node(self, state: AgentState) -> dict[str, Any]:
        """每轮只做一次模型调用，正文不在这里回放。

        有工具调用时，这一轮的文本是要进思考面板的中间叙述；没有工具调用时它只是「候选的最终
        回答」，还要经 review 裁决（可能被打回继续补栏）。在这里逐字推出去，用户会先看到一句
        「已完成」，再看到后面补生成的内容拼上去。
        """
        logger.info("[Agent] 第 %s 步 / 上限 %s", state["step"] + 1, MAX_AGENT_STEPS)
        response = await self.model_with_tools.ainvoke(
            state["messages"], config={"callbacks": self.model_callbacks}
        )

        text = _text_of(response)
        if getattr(response, "tool_calls", None) and text.strip():
            # 中间轮也可能带建议块（模型没收住），剥掉再进思考面板，别让标记当成步骤文案
            self.emit({"type": "thinking", "content": split_suggestions(text, [])[0]})
        return {"messages": [response], "step": 1}

    async def _tools_node(self, state: AgentState) -> dict[str, Any]:
        """逐个执行上一条回复里的工具调用。

        `ask_choice` 独占本轮：模型常把跳转、建记录这类后续动作甚至第二张卡片和它并行发出——
        用户还没点选，页面就先跳了。所以同批只执行第一张卡片，卡片弹出后本轮其余调用作废；
        作废的调用不执行，但必须回一条 `ToolMessage`（每个 tool_call_id 都要有响应，
        否则下一轮的消息历史非法）。
        """
        last = state["messages"][-1]
        calls = list(getattr(last, "tool_calls", None) or [])
        batch_has_choice = any(call["name"] == ASK_CHOICE_TOOL for call in calls)
        results: list[BaseMessage] = []
        suspended = False
        choice_executed = False

        for call in calls:
            name = str(call["name"])
            args = call.get("args") or {}
            call_id = str(call.get("id") or "unknown")

            if self.choice_asked or (batch_has_choice and (name != ASK_CHOICE_TOOL or choice_executed)):
                logger.info("[Agent] 选项卡片已弹出，本轮作废该调用：%s", name)
                results.append(ToolMessage(content=CHOICE_CANCELLED, tool_call_id=call_id))
                continue

            tool = self.tool_map.get(name)
            if tool is None:
                logger.warning("[Agent] 模型调用了未注册的工具：%s", name)
                results.append(ToolMessage(content=f'工具 "{name}" 不存在。', tool_call_id=call_id))
                continue

            # 需要审批的工具先挂起：不执行，落一条待确认记录并把卡片推给用户
            if policy.get_tool_approval(name) == "require_approval":
                view = policy.build_confirmation_view(name, args)
                record = ConfirmationRequest(
                    conversation_id=self.conversation.id,
                    user_id=self.user.id,
                    tool_name=name,
                    args=args,
                    risk_level=policy.get_tool_risk(name),
                    status="pending",
                    title=view["title"],
                    summary=view["summary"],
                )
                self.session.add(record)
                await self.session.flush()
                self.emit(
                    {
                        "type": "confirmation",
                        "confirmation_id": record.id,
                        "tool_name": record.tool_name,
                        "risk_level": record.risk_level,
                        "title": record.title,
                        "summary": record.summary,
                        "data": view["data"],
                    }
                )
                results.append(ToolMessage(content=AWAITING_CONFIRMATION, tool_call_id=call_id))
                suspended = True
                continue

            logger.info(
                "[Agent] 调用工具 %s：%s", name, json.dumps(args, ensure_ascii=False, default=str)[:200]
            )
            self.sse_handler.begin_tool(name)
            try:
                output = str(await tool.ainvoke(args, config={"callbacks": self.tool_callbacks}))
            except Exception as exc:
                logger.warning("[Agent] 工具 %s 执行失败: %s", name, exc, exc_info=True)
                results.append(ToolMessage(content=_tool_error_feedback(name, exc), tool_call_id=call_id))
                continue

            if name == ASK_CHOICE_TOOL:
                choice_executed = True
                # 卡片校验没过（返回的就是错误文案）不封住本轮，允许模型修正选项后重发
                self.choice_asked = self.choice_asked or not output.startswith("错误")
            results.append(ToolMessage(content=output, tool_call_id=call_id))

        await self._save_progress()
        return {"messages": results, "await_confirmation": suspended}

    def _review_node(self, state: AgentState) -> dict[str, Any]:
        """收尾闸门：模型想结束（没有工具调用）时先核对两件事，任一不成立就打回继续。

        ① 上一条回复被输出长度上限截断：那批工具调用是残缺 JSON，实际一个字段都没填，
           不能当成「回答完了」——这就是「跑了几步之后回一句空正文」的成因。
        ② 整篇代填还有空栏：按 id 列出剩余字段逼它补，或明确声明是用户要求留空。

        做成图上的节点而不是「模型可调用的复查工具」：工具要靠模型自觉去调，靠提示词约束
        解决不了自己依赖的那一环。
        """
        last = state["messages"][-1]
        missing = self._pending_fields()
        cut = str(getattr(last, "response_metadata", {}).get("finish_reason") or "") == "length"
        room = not state["await_confirmation"] and state["step"] < MAX_AGENT_STEPS

        if cut and room and state["length_cut_count"] < MAX_LENGTH_CUTS:
            logger.info(
                "[Agent] 输出截断，打回第 %s 次 / 共 %s 次，未填 %s 栏",
                state["length_cut_count"] + 1,
                MAX_LENGTH_CUTS,
                len(missing),
            )
            self.emit({"type": "thinking", "content": "上一轮输出超长被截断，改为逐栏继续生成"})
            return {
                "review_intercept": True,
                "length_cut_count": state["length_cut_count"] + 1,
                "messages": [HumanMessage(truncation_reminder(missing, self.groups))],
            }

        review_needed = (
            bool(missing)
            and not cut
            and room
            and self.fill_events >= WHOLE_PLAN_MIN_FILLS
            and state["review_count"] < MAX_AGENT_REVIEWS
        )
        if not review_needed:
            # 截断重发用尽还是没有正文：记下来，收尾兜底文案要说的是「超长」而不是「换个说法」
            if cut:
                self.length_cut_exhausted = True
            self._emit_final_answer(last)
            return {"review_intercept": False}

        logger.info(
            "[Agent] 复查第 %s 次 / 共 %s 次，未填：%s",
            state["review_count"] + 1,
            MAX_AGENT_REVIEWS,
            ",".join(field.id for field in missing),
        )
        self.emit({"type": "thinking", "content": f"复查：还有 {len(missing)} 栏未填写，继续生成"})
        return {
            "review_intercept": True,
            "review_count": state["review_count"] + 1,
            "messages": [HumanMessage(review_reminder(missing, self.groups))],
        }

    def _emit_final_answer(self, message: BaseMessage) -> None:
        """正文按原样切块推 token，尾部建议块另发一条事件给前端渲染成下一步。"""
        self.emit({"type": "tool", "tool_type": "chat"})
        content, suggestions = split_suggestions(_text_of(message), self.suggestions)
        for piece in _chunks(content, FINAL_CHUNK_CHARS):
            self.emit({"type": "token", "content": piece})
        if suggestions:
            self.emit({"type": "suggestions", "items": suggestions})

    def _pending_fields(self) -> list[Any]:
        """还没填的栏目。判据直接读本轮的表单值：它既含模型填的，也含用户手动填过的。

        `available_fields` 在前端已经滤掉课题位、纯标签与分隔条，剩下的每一项都本该由模型填。
        """
        return [field for field in self.fields if not str(self.values.get(field.id) or "").strip()]

    # ── 本轮准备 ──

    def _prepare_models(self, spec: ModelSpec) -> None:
        """本轮用的三个模型入口与两条回调链。

        兜底链要到有工具清单之后才能拼（工具必须绑在每个模型自己身上，
        见 `_bind_tools_with_fallback`），所以这里只备好主模型；图片解析模型整轮只调一次，
        之后每一步只带它产出的文字，不用流式也不用开思考。
        """
        self.primary_model = build_model(spec, streaming=False)
        self.vision_model = build_model(spec, streaming=False, thinking=False)

        tracer = LangChainTracer(project_name=settings.langchain_project) if is_tracing_enabled() else None
        self.model_callbacks = [tracer] if tracer else []
        self.sse_handler = SSECallbackHandler(self.emit)
        # 工具执行挂 SSE handler（toolCall / toolResult），模型调用只挂 tracer：
        # 正文 token 由 agent 节点统一回放，保证思考与正文一致、不泄漏中间叙述
        self.tool_callbacks = [self.sse_handler, *self.model_callbacks]

    async def _summarize(self, older: list[Message], model: BaseChatModel) -> str:
        """长对话时把切掉的那段历史压成摘要：结构化输出强制模型必须给出一段文字。"""
        lines: list[str] = []
        chars = 0
        for message in reversed(older):
            speaker = "用户" if message.role == "user" else "助手"
            line = f"{speaker}: {str(message.content)[:SUMMARY_LINE_CHAR_LIMIT]}"
            if chars + len(line) > SUMMARY_INPUT_CHAR_LIMIT:
                break
            chars += len(line)
            lines.insert(0, line)

        try:
            structured = model.with_structured_output(ConversationSummary)
            summary = await structured.ainvoke(
                [
                    SystemMessage(
                        "请用简洁的中文概括这段对话的核心主题、用户需求与已得出的结论，输出为结构化摘要。"
                    ),
                    HumanMessage("\n".join(lines)),
                ],
                config={"callbacks": self.model_callbacks},
            )
            # 绑定了 Pydantic 模型，收回的就是它的一个实例，这里只是把类型落回去
            return str(cast("ConversationSummary", summary).summary or "")
        except Exception as exc:
            logger.warning("[Agent] 历史摘要失败，本轮只带最近若干条原文: %s", exc)
            return ""

    async def _append_confirmation_context(self, messages: list[BaseMessage]) -> None:
        """用户对上一轮挂起的工具调用做出了决定：批准就按当初那份参数执行，拒绝就把意见回给模型。

        这里要补两条消息（一条带 tool_calls 的助手消息 + 一条 ToolMessage）而不是只塞一句人文：
        上一轮那条助手消息已经在 checkpoint 里，本轮消息历史必须自洽，否则模型看到的是一段
        没有下文的工具调用。
        """
        confirmation = self.request.confirmation
        if confirmation is None:
            return

        record = await self.session.scalar(
            select(ConfirmationRequest).where(ConfirmationRequest.id == confirmation.confirmation_id)
        )
        if record is None or record.user_id != self.user.id or record.status != "pending":
            messages.append(HumanMessage("（该确认请求已失效或已处理，请重新发起操作。）"))
            return

        call_id = f"resume-{record.id}"
        args = record.args or {}
        if confirmation.decision == "approved":
            record.status = "approved"
            record.decided_at = datetime.now(UTC)
            tool = self.tool_map.get(record.tool_name)
            if tool is None:
                output = f'工具 "{record.tool_name}" 不存在，无法执行。'
            else:
                self.sse_handler.begin_tool(record.tool_name)
                try:
                    output = str(await tool.ainvoke(args, config={"callbacks": self.tool_callbacks}))
                except Exception as exc:
                    logger.warning("[Agent] 批准后执行失败: %s", exc, exc_info=True)
                    output = "工具执行出错，请稍后再试"
        else:
            record.status = "rejected"
            record.feedback = confirmation.feedback
            record.decided_at = datetime.now(UTC)
            output = (
                f"用户没有批准该操作，并给出修改意见：{confirmation.feedback}。请据此调整方案，再重新发起工具调用。"
                if confirmation.feedback
                else "用户没有批准该操作。请调整方案后重新发起，或取消该任务。"
            )

        await self.session.flush()
        messages.append(
            AIMessage(content="", tool_calls=[{"name": record.tool_name, "args": args, "id": call_id}])
        )
        messages.append(ToolMessage(content=output, tool_call_id=call_id))

    # ── 入口 ──

    async def execute(self) -> None:
        spec = await self.configs.spec()
        if not spec.is_configured:
            self.emit(
                {"type": "error", "content": "尚未配置模型 API Key，请在「系统设置 · AI 助手配置」中填写"}
            )
            return

        self.conversation = await resolve_conversation(
            self.session,
            self.user.id,
            self.request.conversation_id,
            first_message=self.request.message,
            lesson_plan_id=self.request.lesson_plan_id,
        )
        # 先取历史再落库本轮用户消息：历史里不该含本轮输入，它后面单独拼在消息末尾
        history = await load_history(self.session, self.conversation.id, HISTORY_SCAN_LIMIT)
        await save_message(
            self.session,
            self.conversation.id,
            self.user.id,
            "user",
            self.request.message,
            attachments=attachment_dicts(self.request.attachments),
        )
        await self.session.commit()

        self._prepare_models(spec)

        # 有已向量化内容时每轮无条件查一次向量库：检索不能依赖模型自觉，
        # 命中注入系统提示词，同时注册检索工具供它换词深挖
        has_rag = await self.rag.has_ready_content(self.user.id)
        rag_hits: list[RagHit] = []
        if has_rag:
            try:
                rag_hits = await self.rag.search(self.request.message, self.user.id, RAG_PRESEARCH_TOP_K)
            except Exception as exc:
                # 前置检索失败不阻断对话（嵌入端点抖动等），降级为无资料上下文
                logger.warning("[Agent] 前置检索失败，本轮不带资料上下文: %s", exc)

        # 新建流程的状态机：跨轮记进度，工具层据此拒绝乱序调用。
        # 表单模式（用户已在新增/编辑页）下整条链不成立，登记要清掉，点选对号也不做——
        # 否则「帮我生成整篇教案」里恰好含模板名时会被误判成「用户刚选了这张卡」。
        self.flow = _PLAN_FLOWS.start_turn(
            self.conversation.id, self.request.message, in_form_mode=bool(self.fields)
        )

        self.tools = build_agent_tools(
            self.request,
            ToolContext(
                session=self.session,
                user_id=self.user.id,
                username=self.user.username,
                storage=self.storage,
                plan_flow=self.flow,
                rag_search=(
                    (lambda query, top_k=RAG_PRESEARCH_TOP_K: self.rag.search(query, self.user.id, top_k))
                    if has_rag
                    else None
                ),
            ),
            self.emit,
        )
        self.tool_map = {tool.name: tool for tool in self.tools}
        backups = build_backup_models(spec, streaming=False)
        self.model_with_tools = _bind_tools_with_fallback(self.primary_model, backups, self.tools)
        self.suggestions = suggestion_candidates(self.request)

        system_prompt = await build_system_prompt(self.request, rag_hits)
        if any((item.mimetype or "").startswith("image/") for item in (self.request.attachments or [])):
            self.emit({"type": "thinking", "content": "正在识别图片内容…"})
        # 本轮用户消息先组装再算预算：图片在这一步已经变成文字，长度要占额度
        current_message = await build_user_message(
            self.request.message, self.request.attachments, self.session, self.storage, self.vision_model
        )
        fixed_tokens = (
            count_tokens(system_prompt)
            + count_message_tokens(_text_of(current_message))
            + TOOL_SCHEMA_TOKEN_RESERVE
            + SUMMARY_TOKEN_RESERVE
        )
        older, recent = split_history_by_budget(
            history, max(CONTEXT_TOKEN_BUDGET - fixed_tokens, MIN_HISTORY_BUDGET)
        )
        summary = await self._summarize(older, self.primary_model) if older else ""

        # 用户点选过的模板编号注入本轮上下文：模型跨轮记不住编号，不告诉它就只能重查或瞎填
        chosen = self.flow.template
        flow_hint = (
            ""
            if chosen is None
            else (
                f"\n\n【新建教案流程】用户已选定模板「{chosen.name}」（内部编号 {chosen.id}），"
                f"调用 open_lesson_plan_create / create_lesson_plan 时 template_id 必须传 {chosen.id}。"
            )
        )
        final_prompt = system_prompt
        if summary:
            final_prompt += f"\n\n【历史对话摘要】\n{summary}\n请基于以上摘要继续对话，不要重复已讨论的内容。"
        final_prompt += flow_hint

        messages: list[BaseMessage] = [
            SystemMessage(final_prompt),
            *build_history_messages(recent),
            current_message,
        ]
        logger.info(
            "[Agent] 上下文预算 %s tok，固定占 %s tok，历史带 %s 条 / 压成摘要 %s 条",
            CONTEXT_TOKEN_BUDGET,
            fixed_tokens,
            len(recent),
            len(older),
        )

        await self._append_confirmation_context(messages)

        graph = (
            StateGraph(AgentState)
            .add_node("agent", self._agent_node)
            .add_node("tools", self._tools_node)
            .add_node("review", self._review_node)
            .add_edge(START, "agent")
            .add_conditional_edges("agent", _route_after_agent, {"tools": "tools", "review": "review"})
            .add_edge("tools", "agent")
            .add_conditional_edges("review", _route_after_review, {"agent": "agent", "end": END})
            .compile(checkpointer=_CHECKPOINT)
        )
        # 每轮一个独立 thread：跨轮历史由消息表 + 摘要承担，接进 checkpoint 会让 reducer
        # 把新旧历史拼接重复。一个 agent 步 = agent + tools 两个超步，默认 25 在第 12 步就炸，
        # 所以按步数上限折算放闸，再给复查与截断打回留余量；超出步数仍由路由主动收尾。
        thread_id = f"conv-{self.conversation.id}-{int(time.time() * 1000)}"
        try:
            final_state = await graph.ainvoke(
                {
                    "messages": messages,
                    "step": 0,
                    "await_confirmation": False,
                    "review_count": 0,
                    "length_cut_count": 0,
                    "review_intercept": False,
                },
                {"recursion_limit": _recursion_limit(), "configurable": {"thread_id": thread_id}},
            )
        finally:
            _CHECKPOINT.delete_thread(thread_id)

        # LangGraph 收回的是普通 dict，形状就是 AgentState，只差在类型上
        answer = self._final_answer(cast("AgentState", final_state))
        if not answer:
            answer = self._fallback_answer()
            # 补发一条 token：前端气泡只由 token 累积，否则界面上是一片空白，
            # 这句兜底文案只有刷新后才看得到
            self.emit({"type": "token", "content": answer})

        message_id = None
        if answer:
            saved = await save_message(
                self.session,
                self.conversation.id,
                self.user.id,
                "assistant",
                answer,
                generated_files=self.generated_files or None,
                # 落库前清掉过程态：历史消息读出来不该显示「进行中」
                steps=[{**step, "pending": False} for step in self.steps] or None,
            )
            message_id = saved.id
        await self._save_progress()

        self.emit({"type": "done", "message_id": message_id, "conversation_id": self.conversation.id})

    def _final_answer(self, state: AgentState) -> str:
        """从终态取出助手的最终回答：建议块只给本轮前端渲染，落库的历史必须是干净正文，
        否则下一轮模型会把标记当成内容学走。
        """
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            return ""
        return split_suggestions(_text_of(last), self.suggestions)[0]

    def _fallback_answer(self) -> str:
        """循环跑完却没有正文时的收尾文案：三种成因要说的是三句话，不能混成一句「换个说法再试」。"""
        if self.outline_fallback is not None:
            return (
                f"大纲已生成，共 {self.outline_fallback['count']} 页：{self.outline_fallback['titles']}。"
                "请确认后我开始逐页生成。"
            )
        remaining = len(self._pending_fields())
        filled = len(self.fields) - remaining
        if self.length_cut_exhausted:
            return (
                "单次输出长度上限把这一轮截断了，我停下来以免重复覆盖。"
                f"表单里已有 {filled} 栏内容，还差 {remaining} 栏。"
                "回复「继续」我接着填剩下的；想让一轮多填几栏，可在 AI 配置里调高最大输出 token。"
            )
        return (
            "这一轮没能得到结果，我没有继续往下做。"
            "可以换个说法，或让我先列出资料库文件、先读某个教案再试一次。"
        )


def _recursion_limit() -> int:
    return MAX_AGENT_STEPS * 2 + (MAX_AGENT_REVIEWS + MAX_LENGTH_CUTS) * 2 + 7


class AgentService:
    """接口层进来的那扇门：每轮一个实例，进程级共享的东西不放在这里。"""

    def __init__(self, storage: StorageService) -> None:
        self.storage = storage

    async def run(self, request: AgentChatInput, user: CurrentUser, emit: Emit) -> None:
        """跑完一轮。异常与心跳都由 SSE 通道兜住，这里只管业务。"""
        # SSE 一开始输出，请求级会话就随依赖退出被关掉了，所以本轮自带一个会话：
        # 里面既读教案与课件，也写消息与自动保存的表单值，跑完一起提交。
        async with SessionFactory() as session:
            await AgentTurn(request, user, session, self.storage, emit).execute()


def get_agent_service(storage: StorageDep) -> AgentService:
    return AgentService(storage)


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]
