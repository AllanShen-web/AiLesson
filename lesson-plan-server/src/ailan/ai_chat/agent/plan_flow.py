"""新建教案流程的跨轮进度。

一次「新建教案」要跨好几轮对话：列模板 → 一次问完选模板+选创建方式 → 跳转新增页或建档生成。
模型对这条顺序时灵时不灵——忘了就会直接建档、直接跳转，或者替用户挑模板。所以顺序校验放在
工具层：乱序调用当场打回并告诉它缺哪一步，比祈祷模型遵守提示词可靠。

进度存在进程内存里，按会话 id 归位。重启或超时即失，最坏情况是模型从头再走一遍流程，
不碰任何数据。工具返回也不跨轮保留，所以卡片点选必须由服务端按消息原文对号：
弹卡时记下候选清单，下一轮用户消息逐行命中即视为选定，模板连内部编号一起解析出来。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

FlowStep = Literal["listed", "template_picked", "method_picked"]

# 只前进不回退：模型重查模板清单是正常行为，不能把已推进的进度抹掉，
# 否则「方式卡」会反复弹，用户点完又回到原点
_STEP_RANK: dict[FlowStep, int] = {"listed": 1, "template_picked": 2, "method_picked": 3}

# 一条新建流程两小时没动静就当作放弃了
TTL_SECONDS = 2 * 60 * 60
# 会话数上限：内存里的进程态不该无界增长，超了先丢掉最早登记的那条
MAX_SESSIONS = 200


@dataclass(frozen=True)
class TemplateChoice:
    """模板候选的一项：编号只由服务端解析，模型传进来的一律不信。"""

    id: int
    name: str


@dataclass
class _Entry:
    step: FlowStep = "listed"
    templates: list[TemplateChoice] = field(default_factory=list)
    template: TemplateChoice | None = None
    method_options: list[str] = field(default_factory=list)
    touched_at: float = field(default_factory=time.monotonic)


class PlanFlowStore:
    """进程内的流程登记表。每个请求轮次取一个 `PlanFlow` 视图，工具只认视图。"""

    def __init__(self) -> None:
        self._entries: dict[int, _Entry] = {}

    def start_turn(self, conversation_id: int, message: str, *, in_form_mode: bool) -> PlanFlow:
        """开始一轮：表单模式下整条新建流程不成立，直接清掉同会话残留的状态；
        否则先拿用户消息和上一轮记下的候选对号，把点选结果转成进度。
        """
        if in_form_mode:
            self._entries.pop(conversation_id, None)
        else:
            self._match_choice(conversation_id, message)
        return PlanFlow(self, conversation_id)

    def _entry(self, conversation_id: int) -> _Entry | None:
        entry = self._entries.get(conversation_id)
        if entry is None:
            return None
        if time.monotonic() - entry.touched_at > TTL_SECONDS:
            self._entries.pop(conversation_id, None)
            return None
        return entry

    def _ensure(self, conversation_id: int) -> _Entry:
        entry = self._entry(conversation_id)
        if entry is None:
            if len(self._entries) >= MAX_SESSIONS:
                self._entries.pop(next(iter(self._entries)), None)
            entry = _Entry()
            self._entries[conversation_id] = entry
        entry.touched_at = time.monotonic()
        return entry

    def _match_choice(self, conversation_id: int, message: str) -> None:
        """点卡片就是把选项文字原样发来，所以只认整行逐字命中。

        用「包含」判会出事：「用《单元模板》写一篇《草原》的教案」里顺带提到模板名，
        会被当成用户刚点了这张卡，模板卡从此再也不会弹。
        一卡多问时前端按问的顺序把各问选中的文字拼成一条消息（换行分隔），逐行对号可一次推进多个状态。
        """
        entry = self._entry(conversation_id)
        if entry is None or not message.strip():
            return
        for line in (item.strip() for item in message.splitlines()):
            if not line:
                continue
            hit = next((item for item in entry.templates if item.name == line), None)
            if hit is not None:
                entry.template = hit
                self.advance(conversation_id, "template_picked")
            elif line in entry.method_options:
                self.advance(conversation_id, "method_picked")

    def advance(self, conversation_id: int, step: FlowStep) -> None:
        entry = self._ensure(conversation_id)
        if _STEP_RANK[step] > _STEP_RANK[entry.step]:
            entry.step = step

    def drop(self, conversation_id: int) -> None:
        self._entries.pop(conversation_id, None)


class PlanFlow:
    """一次请求里工具看到的流程视图：所有读写都落在同一个会话的登记项上。"""

    def __init__(self, store: PlanFlowStore, conversation_id: int) -> None:
        self._store = store
        self._conversation_id = conversation_id

    @property
    def step(self) -> FlowStep | None:
        entry = self._store._entry(self._conversation_id)
        return entry.step if entry else None

    @property
    def method_chosen(self) -> bool:
        return self.step == "method_picked"

    @property
    def template(self) -> TemplateChoice | None:
        entry = self._store._entry(self._conversation_id)
        return entry.template if entry else None

    @property
    def templates(self) -> list[TemplateChoice]:
        entry = self._store._entry(self._conversation_id)
        return list(entry.templates) if entry else []

    def set_step(self, step: FlowStep) -> None:
        self._store.advance(self._conversation_id, step)

    def note_templates(self, templates: list[TemplateChoice]) -> None:
        """记下这次列出来的模板：下一轮按用户点选的文字对号取编号。"""
        entry = self._store._ensure(self._conversation_id)
        # 重新列模板时，已选定的那个若不在新清单里就作废（用户改主意由点选消息重新解析覆盖）
        if entry.template and not any(item.id == entry.template.id for item in templates):
            entry.template = None
        entry.templates = templates

    def note_method_options(self, labels: list[str]) -> None:
        self._store._ensure(self._conversation_id).method_options = labels

    def choose_template(self, template: TemplateChoice) -> None:
        """直接改写已选定模板：拷贝公有模板后指向拷出来的那一份。"""
        self._store._ensure(self._conversation_id).template = template

    def clear(self) -> None:
        """建档或跳转已经落地，这条流程走完了，下一轮重新开始。"""
        self._store.drop(self._conversation_id)
