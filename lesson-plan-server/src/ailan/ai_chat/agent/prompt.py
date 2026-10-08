"""助手发给模型的所有文字：系统提示词、复查提醒与引导建议。

系统提示词是「静态规则 + 运行时上下文」拼出来的：规则固化在模板里（能整段 review、
能整段版本管理），课题、字段清单、已填内容这些每轮都变的东西作为片段注入。
片段是不是注入，取决于本轮注册了哪些工具——表单模式下没有「选模板 → 选创建方式」这条链，
配套的新建教案规则也一并撤掉，否则模型会被两套规则拉扯着往用户脸上弹选择卡片。

字段清单在这里统一渲染成给模型看的文字。同一模板的两个课时里栏目名成对重复
（两栏都叫「效果评价」），只报 label 模型分不清是哪一栏，所以分组名和重复标记都在
 `FieldGroups` 一处算清，提示词、复查提醒、工具返回值都用它。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from ailan.ai_chat.schemas import AgentChatInput, AgentField
from ailan.rag.schemas import RagHit

# 大纲页数上限：只挡模型乱填（一次报几百页），不是产品限制。
# 页面按每批 3~4 页分批生成，50 页约 13 批，仍在单轮步数预算内
MAX_PPT_OUTLINE_SECTIONS = 50

# 引导建议块的标记：正文最后一行，前端渲染成可点击的下一步
SUGGESTION_OPEN = "<suggestions>"
SUGGESTION_CLOSE = "</suggestions>"

# 课时分组边界：标题形如「第一课时 / 第二课时 / 课时1 / 3学时」的纯栏目标题
_PERIOD_BOUNDARY = re.compile(r"^第?\s*[一二两三四五六七八九十0-9１-９]+\s*(?:课时|学时)")

# 表单页与教案记录两条代填链的工具名不同，提醒文字要跟上本轮真正有的那个
_FORM_FILL_TOOL = "fill_form_field"
_PLAN_FILL_TOOL = "fill_lesson_plan_fields"

_BASIC_INFO_LABELS = {"topic": "课题", "unit": "单元", "subject": "学科", "grade": "年级"}

# 落进提示词里的字段清单每行都带分组，超过这个数才值得费口舌解释分组规则
_SEGMENT_MIN_GROUPS = 2


@dataclass(frozen=True)
class FieldGroups:
    """教案栏目的分组视图：栏目属于哪个课时/分段，以及它在清单里该怎么写。

    分组有两个来源：编辑器随请求带上来（`from_request`，前端按模板完整结构算好的），
    和新建教案时从模板结构现算（`from_template`，此时还没有前端上下文）。
    两种来源走同一套边界规则，模型看到的清单口径才一致。
    """

    labels: dict[str, str]
    types: dict[str, str]
    group_of: dict[str, str]
    total: int

    @classmethod
    def from_request(cls, fields: Sequence[AgentField]) -> FieldGroups:
        return cls(
            labels={field.id: field.label for field in fields},
            types={field.id: field.type for field in fields},
            group_of={field.id: field.group for field in fields if field.group},
            total=len(fields),
        )

    @classmethod
    def from_template(cls, fields: Iterable[dict[str, Any]]) -> FieldGroups:
        """按模板结构划组：栏目标题里的「第X课时」是边界，连续两个分隔符也算边界。

        后者是给「用户把课时标题删了、只留分页」那种模板留的路——一套字段照样分两段。
        """
        items = [field for field in fields if isinstance(field, dict)]
        group_of: dict[str, str] = {}
        labels: dict[str, str] = {}
        types: dict[str, str] = {}
        current: str | None = None
        part_seq = 1
        divider_run = 0
        fillable = 0
        for field in items:
            field_id = str(field.get("id") or "")
            field_type = str(field.get("type") or "textarea")
            label = str(field.get("label") or "").strip()
            if field_id:
                labels[field_id] = label
                types[field_id] = field_type
            if field_type == "divider":
                divider_run += 1
                continue
            if divider_run >= 2:
                part_seq += 1
                current = f"第{part_seq}部分"
            divider_run = 0
            if field_type == "label":
                if _PERIOD_BOUNDARY.match(label):
                    current = label
                    part_seq += 1
                continue
            fillable += 1
            if field_id and current:
                group_of[field_id] = current
        return cls(labels=labels, types=types, group_of=group_of, total=fillable)

    @property
    def has_groups(self) -> bool:
        return bool(self.group_of)

    @property
    def segmented(self) -> bool:
        """分段模板：两个及以上分组，或「有分组但仍有未归组栏目」（只剩分页的那种）。"""
        names = set(self.group_of.values())
        ungrouped = self.total - len(self.group_of)
        return bool(names) and (len(names) >= _SEGMENT_MIN_GROUPS or ungrouped > 0)

    @property
    def is_period(self) -> bool:
        return any("课时" in name or "学时" in name for name in self.group_of.values())

    def display_name(self, field_id: str) -> str:
        """带分组前缀的可读名：`第二课时·效果评价`；没有分组就是裸 label。"""
        label = self.labels.get(field_id) or field_id
        group = self.group_of.get(field_id)
        return f"{group}·{label}" if group else label

    def title(self, field_id: str) -> str:
        """提示词里指代某一栏的写法：同名栏目补 id，否则模型分不清是哪一栏。"""
        label = self.labels.get(field_id) or field_id
        if self.group_of.get(field_id):
            return f"{label}（{self.group_of[field_id]}）"
        duplicated = [key for key, value in self.labels.items() if value == label and key != field_id]
        return f"{label}（{field_id}）" if duplicated else label

    def lines(self, field_ids: Iterable[str], *, with_type: bool = False) -> str:
        """按给出顺序渲染清单，分组变化时插一行【组名】，与模型看到的先后一致。"""
        out: list[str] = []
        last_group: str | None = None
        for field_id in field_ids:
            group = self.group_of.get(field_id)
            if group and group != last_group:
                out.append(f"【{group}】")
            last_group = group
            label = self.labels.get(field_id) or field_id
            suffix = f" ({self.types.get(field_id)})" if with_type and self.types.get(field_id) else ""
            out.append(f"  - {field_id}: {label}{suffix}")
        return "\n".join(out)


# ── 系统提示词 ──

# 新建教案流程（表单之外）的规则：只在没有可填字段时注入。
# 与工具注册同源——表单模式下那套工具本来就不注册，规则再留着只会诱导模型弹选择卡片
_NEW_PLAN_RULES = """新建教案规则（用户想创建一篇新教案、而不是编辑当前表单里的教案时，严格按此流程）：
1. 本流程顺序固定为：列模板 → 一次问完「选模板 + 选创建方式」→ 执行。系统按状态机校验每一步，
   乱序调用会被工具直接打回，被打回时不要争辩，按返回的指引补上前一步
2. 凡是要用户在几个方案中做选择，一律调用 ask_choice 弹选项卡片，严禁在正文里罗列选项要用户打字回复。
   ask_choice 一次调用可以带多个问题（questions 数组，每项各自带 kind/question/options），前端逐问显示、
   用户点完最后一问才把所有选择作为一条消息发回。它必须单独一轮调用：严禁在同一次回复里把它和任何其他工具一起发出
3. 先调用 list_lesson_templates 拿到模板清单（严禁凭记忆罗列模板）。然后**一次** ask_choice 问完两件事：
   questions 传两项——第一项 kind="template"，把清单里的每个模板列为选项（label 逐字照抄清单里的模板名，
   不要加任何前后缀，系统按 label 对号解析模板编号；栏目数和「公有模板」写进 description）；
   第二项 kind="method"，两个选项：
   「去教案新增页创建教案」（description：跳转表单页，自动填好基础信息，核对确认后逐栏生成）
   「直接生成 Word 教案文件」（description：对话里直接成稿，同时存入教案列表并可下载）
   每次新建都要问模板，严禁替用户挑（即使用户消息里报了模板名，也要请他点选，系统只认点选结果，
   未点选的模板会被工具打回）；只有一个模板时也要把它作为唯一选项列出来请用户点选确认，不许因为没得选就跳过；
   也严禁把这两问拆成两轮——模板还没定就只问创建方式会被工具打回。
   清单里标「公有模板」的那几项：用户点选了它，先调用 copy_template 拷成用户自己的一份（返回新编号），再往下执行。
   用户点选后，下一轮系统会在上下文里告知已选模板的名称与内部编号——直接采用该编号，严禁凭记忆填
4. 用户点选后，按他选的创建方式执行下面两条对应的那一条：
5. 用户选「去教案新增页」→ 调用 open_lesson_plan_create，务必把已知课题传入 topic（学科/年级有把握才传，
   没有就留空，页面会按课题自动回填），然后收尾：告知页面已打开、基础信息已自动填好，
   请用户核对确认后让你开始生成整篇教案（跳转后表单页接管，由 fill_form_field 逐栏代填）
6. 用户选「直接生成 Word 教案文件」→ 调用 create_lesson_plan(template_id, title, subject, grade) 建记录，
   按返回的栏目清单分批调用 fill_lesson_plan_fields 生成内容（一轮并列 2~3 个调用，等这批返回再发下一批，
   严禁遗漏任何栏目）；全部栏目填完后调用 export_lesson_plan(lesson_plan_id, format="docx") 生成文件
7. 生成内容前可先检索资料库作为素材（search_knowledge_base / read_knowledge_file / search_knowledge_chunks）；
   内容只能落在模板自带的栏目里，严禁编造模板之外的栏目
8. 模板与创建方式一旦由用户选定，后续步骤中途不要停下来再征求同意

"""

_SYSTEM_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你是Ailan智慧教案平台的智慧助手布布。你可以使用工具来完成用户的任务。

{context_section}{content_section}{fields_section}{selected_field_section}{lesson_plan_context_section}{ppt_context_section}{confirmation_section}{rag_section}工作原则：
1. 分析用户的需求，选择合适的工具
2. 工具执行后你会看到真实结果，据此决定下一步
3. 只有当你确认任务完成时，才直接回复用户（不再调用工具）
4. 生成内容时确保与教案信息（课题、学科等）一致

{form_rules_section}检索规则：
- 当用户明确要求查看、搜索、查找教案或资料库时，调用检索工具
- 搜索结果只返回摘要，需要详情时再调用读取内容的工具
- 用户的问题涉及资料库文件的具体内容（概念、数据、段落细节）时，优先调用 search_knowledge_chunks 做语义检索
- 按文件名浏览、列出资料库文件时用 search_knowledge_base / read_knowledge_file
- 引用检索到的片段时说明来源资料名称，严禁在资料之外编造内容

导出规则：
- 当用户要求导出、下载、生成文件时，调用 export_lesson_plan
- 「生成word/PDF/文件」只是要一个文件，不是要写内容：直接用现有内容调用 export_lesson_plan 出文件，
  严禁先补写、重写或「补齐空栏」任何字段——表单里有空栏不是补写的理由
- 支持格式：docx（word/doc 亦可）、pdf、pptx（PPT 亦可）、md（markdown 亦可）、txt，大小写不敏感
- 教案正文由系统自动取（数据库快照 + 当前表单实时内容），严禁把正文拼进 fields / content 参数回传：
  整篇内容塞不进一次回复，工具调用会被长度上限截断，结果是文件根本没生成
- 在教案表单页里导出当前这一篇，只传 format；导出其他教案时传 lesson_plan_id
  （取自 search_lesson_plans / read_lesson_plan 的返回结果）
- fields（key为字段id，value为内容）和 form_info 仅用于对话里现写的临时稿（既没入库也不在表单里）
- 导出 md/txt 临时内容时可使用 content 参数（纯文本）
- 生成文件后告知用户文件已生成，可以下载

{new_plan_section}PPT 生成规则（先大纲，经用户确认后才生成页面）：
- 当用户要求生成 PPT、幻灯片、课件时，必须严格按以下步骤执行：
  0.（可选）如需感知外部课件风格，先调用 search_ppt_reference；搜不到或失败就直接继续，不要反复重试
  1. 调用 create_ppt_outline：如需教案内容先调用 read_lesson_plan，然后按教案实际篇幅规划页数
     （一般 10~20 页，最多 {max_outline_sections} 页；内容多就多分几页，不要为凑页数拆分，也不要为省事压缩）
     （第 1 页为封面 cover，最后一页为结束页 end，中间为内容页），并做到：
     · 根据教案年级判断学段，传入 stage：小学 primary / 中学 middle / 大学（含高职）college；无法判断时按中学
     · 在 outline 中为每页给出标题、要点，并按本页内容性质指定 layout（见「版式选择」），做到版式多样、不雷同
  2. create_ppt_outline 返回后，本轮必须立即收尾：用自然语言向用户展示大纲（各页标题与要点），
     并说明「确认后开始逐页生成」。系统会弹出确认卡片，严禁在同轮继续调用 generate_ppt_slides
  3. 只有当系统在上下文中明确告知「用户已确认」时，才调用 generate_ppt_slides：
     从第 1 页开始分批（每批 3~4 页，用 from_page/to_page 指定页码范围）连续生成，禁止跳过页码，
     每批返回后继续下一批，直到全部完成；严禁重新创建大纲
- 第一批生成时必须通过 theme 参数指定主题（后续批次可不传），按学科与学段从下列内置主题中选择，
  字段顺序为 primary/accent/bg/title_color/text_color，font 统一「微软雅黑」，style 取 band 或 card：
  · 小学/活泼场景：糖果粉 DB2777/FBCFE8/FDF2F8/831843/3F3F46（band）
  · 小学/语文英语：暖阳橙 EA580C/FDBA74/FFF7ED/7C2D12/292524（band）
  · 中学/通用：海洋蓝 2563EB/93C5FD/FFFFFF/1E3A8A/334155（card）
  · 理科/生物地理：青苗绿 059669/A7F3D0/F0FDF4/064E3B/1F2937（card）
  · 文科/艺术审美：葡萄紫 7C3AED/DDD6FE/FAF5FF/4C1D95/1F2937（card）
  · 大学/公开课/稳重正式：水墨黑 374151/D1D5DB/FFFFFF/111827/374151（plain）
- 版式选择（layout 共 11 种）：按每页内容性质挑选，相邻页尽量不同，不要每页都是标题要点：
  · cover 封面 / toc 目录 / section 章节过渡 / end 结束
  · title-bullets 标题要点：概念、知识点罗列
  · two-column 双栏：并列要点较多
  · big-number 大数字：关键数据、核心结论（提供 big_number，数字醒目）
  · chart 图表：有可比较、趋势或占比的数据（提供 chart，数据合理、单位清楚，数据解读写 bullets）
  · timeline 时间线流程：步骤、流程、发展史（提供 timeline）
  · compare 对比：两类事物异同、优缺点（提供 compare）
  · activity 课堂活动：小组讨论、实验、探究、练习（提供 activity，3~5 步）
- 内容要求：
  · 以教案为事实依据，知识点、数据不得与教案冲突；在此基础上围绕课堂教学合理扩写，不要只照搬标题
  · 主动补足课堂元素：情境导入、提问、例题/案例、课堂活动、板书要点、课堂小结，让课件可直接上课
  · 每页 bullets 3~6 条、每条尽量不超过 40 字，用短句和关键词；图表、活动等元素优先于纯文字堆砌
  · 学段差异：小学多用活动、提问、大数字与生动情境，语言浅显；中学知识与活动均衡；大学信息密度高，
    多用图表、对比与案例，减少花哨装饰
- 用户要求修改已生成 PPT 的某一页时，调用 update_ppt_slide（按页码定位，一次改一页；换版式时同步提供对应富元素）
- 生成开始后简要告知用户「正在逐页生成」，完成后提示可在「PPT课件」页面查看和编辑
{suggestion_section}
回复规范：
- 工具返回结果中的编号、ID、内部字段名（如 official-objectives）都是系统内部标识，严禁出现在给用户的回复中
- 向用户描述教案、资料库或文件时，只使用标题、名称、科目、年级等自然信息
- 用自然的口语描述操作结果，例如「已找到 3 篇相关教案」「秋天的雨.docx 已生成」，不要复述工具返回的原始格式""",
        )
    ]
)


async def build_system_prompt(request: AgentChatInput, rag_hits: list[RagHit] | None = None) -> str:
    """按本轮上下文渲染系统提示词。

    片段有六个开关位：教案信息、已填内容、字段清单、选中字段、表单规则、新建教案规则——
    后两个互斥，判据是「本轮有没有可填字段」，与 `tools.build_agent_tools` 注册工具的判据一致。
    """
    fields = request.available_fields or []
    groups = FieldGroups.from_request(fields)
    in_form_mode = bool(fields)

    rendered = await _SYSTEM_TEMPLATE.ainvoke(
        {
            "context_section": _basic_info_section(request.form_context),
            "content_section": _filled_content_section(request, groups),
            "fields_section": _fields_section(fields, groups),
            "selected_field_section": _selected_field_section(request, groups),
            "lesson_plan_context_section": _lesson_plan_section(request.lesson_plan_id),
            "ppt_context_section": _ppt_section(request.ppt_id),
            "confirmation_section": _confirmation_section(request),
            "rag_section": _rag_section(rag_hits or []),
            "form_rules_section": _form_rules_section(fields, groups) if in_form_mode else "",
            "new_plan_section": "" if in_form_mode else _NEW_PLAN_RULES,
            "max_outline_sections": MAX_PPT_OUTLINE_SECTIONS,
            "suggestion_section": _suggestion_section(suggestion_candidates(request)),
        }
    )
    # 模板只有一条 system 消息，to_string() 取到的就是它的正文
    return rendered.to_string()


def _basic_info_section(form_context: dict[str, str] | None) -> str:
    filled = {key: value for key, value in (form_context or {}).items() if value}
    if not filled:
        return ""
    lines = "\n".join(f"- {_BASIC_INFO_LABELS.get(key, key)}: {value}" for key, value in filled.items())
    return f"当前教案信息：\n{lines}\n\n"


def _filled_content_section(request: AgentChatInput, groups: FieldGroups) -> str:
    entries = [(key, value) for key, value in (request.lesson_plan_content or {}).items() if value]
    if not entries:
        return ""
    blocks = "\n\n".join(f"### {groups.title(key)}\n{value}" for key, value in entries)
    return f"当前教案已有的内容（请基于这些内容进行修改、优化或补充，不要丢弃已有内容）：\n{blocks}\n\n"


def _fields_section(fields: list[AgentField], groups: FieldGroups) -> str:
    if not fields:
        return ""
    if groups.has_groups:
        head = (
            "可用教案字段列表（按模板分组列出；不同【】分组里的同名字段分属不同课时，是各自独立的栏目）：\n"
            + groups.lines([field.id for field in fields], with_type=True)
            + "\n\n"
        )
    else:
        listing = "\n".join(f"  - {field.id}: {field.label} ({field.type})" for field in fields)
        head = f"可用教案字段列表：\n{listing}\n\n"
    return head + (group_rules(groups, _FORM_FILL_TOOL) if groups.segmented else "")


def _selected_field_section(request: AgentChatInput, groups: FieldGroups) -> str:
    """选中字段必须给 id：只给 label 时，模板里的同名字段模型只能猜第一个。"""
    selected = next(
        (field for field in request.available_fields or [] if field.id == request.selected_field_id), None
    )
    if selected is None:
        return ""
    group = groups.group_of.get(selected.id)
    where = f"{group}·{selected.label}" if group else selected.label
    return (
        f"用户当前选中的字段：{selected.id}（{where}）。"
        f"用户说「这个字段」「它」或只报字段名时，"
        f"{_FORM_FILL_TOOL} 的 field_id 就用 {selected.id}；"
        "同名字段属于不同位置，禁止按名称另选其他字段。\n\n"
    )


def _form_rules_section(fields: list[AgentField], groups: FieldGroups) -> str:
    listing = (
        groups.lines([field.id for field in fields])
        if groups.has_groups
        else "\n".join(f"  - {field.id}: {field.label}" for field in fields)
    )
    return f"""表单工具使用规则：
- 用户已在教案表单页（新增或编辑），模板已由用户在页面上选定：这里说的「生成教案」就是把内容填进当前表单，
  不存在选模板、选创建方式、跳转新增页这些环节（本轮也没有对应工具），严禁弹这类选择卡片
- 用户选中某个字段并请求生成 → 调用 {_FORM_FILL_TOOL}，field_id 用上方【用户当前选中的字段】给出的编号，
  在 content 中直接提供完整内容
- 重要：content 参数中严禁包含字段名称、标题或编号（如"【教学目标】""1."等），因为前端已显示字段标签，
  重复会让用户困惑。只需提供纯内容正文。
- 只有用户要生成/改写教案内容时才适用下一条：他要的是文件（导出、下载、生成word）就直接导出，一栏都不要动
- 如需生成/修改多个字段（包括整篇教案），必须为下方【所有字段】各调用一次 {_FORM_FILL_TOOL}，不能遗漏任何字段：
{listing}
- 需要生成多个字段时，一轮之内在同一条回复里并列发出 2~3 个 {_FORM_FILL_TOOL} 调用（每个字段一个），
  按上方顺序推进；等这一批全部返回后再发下一批，直到上方每一项都被调用过为止。一批不要超过 3 个：
  同一条回复的输出长度有上限，塞太多字段会把这一批的工具调用截断，结果是这几个字段一个都没填上
- 每个字段只能调用一次，调用一次即完成该字段

"""


def group_rules(groups: FieldGroups, fill_tool: str) -> str:
    """多课时/多分组模板的规则：防「第二课时的同名栏被当成已填过而跳过」和「把第一课时正文原样贴进第二课时」。"""
    if groups.is_period:
        head = "多课时模板规则（清单里的【】分组来自模板自身的分段，每个分组是一节课时的完整设计）："
    else:
        head = "多分组模板规则（清单里的【】分组来自模板的分页；若模板按课时排布，每个分组即一个课时）："
    bullets = [
        f"- 不同分组里的同名字段是两个独立栏目：填完前一个分组不等于整篇完成，后一个分组的每一栏都必须各自调用一次 {fill_tool}，field_id 用该分组清单里给出的编号",
        "- 严禁把前一分组的正文复制进后一分组。两个课时是同一课题下连续的两节课：前一课时设计本课内容的起始部分，后一课时承接前一课时的基础、设计后续或新内容并体现递进——教学目标、学习活动、效果评价、板书、作业在各课时都应有所不同",
        "- 课标、教材分析（要素挖掘、前后关联等）、学情这类在每组重复出现的分析栏：后一课时提炼要点并聚焦本课时教学内容，不要整段照抄前一课时",
        "- 排在任何【】之前的字段属于模板第一段（第一课时）：它们与后面分组里的同名字段同样是两个独立栏目，必须分别生成",
    ]
    if groups.is_period:
        bullets.append(
            "- 「课时」数字栏按分段顺序分别填 1、2…；「课题」栏各分组可同名，也可在课题后标注（第X课时）"
        )
    return f"{head}\n" + "\n".join(bullets) + "\n\n"


def _lesson_plan_section(lesson_plan_id: int | None) -> str:
    """外部页面带着教案上下文发起请求时（如教案列表的「生成 PPT」入口），
    明确告知目标教案，避免模型再去搜索猜测。"""
    if not lesson_plan_id:
        return ""
    return (
        f"本次请求关联的教案（内部编号 {lesson_plan_id}）。"
        "用户未指定其他教案时，读取、导出、生成 PPT 均使用该教案。\n\n"
    )


def _ppt_section(ppt_id: int | None) -> str:
    if not ppt_id:
        return ""
    return (
        f"用户当前正在编辑 PPT（内部编号 {ppt_id}）。"
        "当用户要求修改、调整某页幻灯片时，使用该编号定位目标。\n\n"
    )


def _confirmation_section(request: AgentChatInput) -> str:
    """用户对某条挂起工具调用做出决定：框架已执行批准的工具或回填拒绝信息，模型据此继续。"""
    confirmation = request.confirmation
    if confirmation is None:
        return ""
    if confirmation.decision == "approved":
        return (
            "用户已批准该操作，工具执行结果已给出。请基于工具结果继续完成后续步骤"
            "（如继续逐页生成 PPT），不要重复调用已批准的工具。\n\n"
        )
    marked = "，修改意见已在工具返回中给出" if confirmation.feedback else ""
    return f"用户拒绝了该操作{marked}。请据此调整方案并重新发起工具调用。\n\n"


def _rag_section(rag_hits: list[RagHit]) -> str:
    """固定前置检索命中的资料库片段：每轮由系统无条件检索注入，不依赖模型自觉调工具。"""
    if not rag_hits:
        return ""
    blocks = "\n\n".join(
        f"{index}. 《{hit.resource_name}》片段：\n{hit.content}" for index, hit in enumerate(rag_hits, 1)
    )
    return (
        "资料库语义检索命中的片段（系统每轮自动检索，内容可能与本问题无关）：\n"
        f"{blocks}\n\n"
        "使用要求：用户的问题涉及其个人资料或上传材料时，优先依据上述片段作答并说明来源文档名；"
        "片段与问题无关则忽略，需要换关键词补查时可调用 search_knowledge_chunks；"
        "严禁把无关片段硬塞进答案。\n\n"
    )


# ── 引导建议：候选由代码给，模型只挑不改写 ──


def suggestion_candidates(request: AgentChatInput) -> list[str]:
    """按本轮真实注册的工具给出候选（豆包式「下一步」chips）。

    候选来自代码而不是模型，从根上保证建议出来的动作一定存在，
    不会出现「引导用户做工具做不到的事」。
    """
    fields = request.available_fields or []
    content = request.lesson_plan_content or {}
    filled = [field for field in fields if content.get(field.id)]
    out: list[str] = []

    if fields:
        selected = next((field for field in fields if field.id == request.selected_field_id), None)
        if selected is not None:
            verb = "优化" if content.get(selected.id) else "生成"
            out.append(f"{verb}【{selected.label}】的内容")
        elif len(filled) < len(fields) / 2:
            out.append("帮我生成整篇教案")
        else:
            out.append("帮我优化整篇教案")
            if len(filled) < len(fields):
                out.append("补齐还没填写的字段")
    # 有教案可导出时才提导出/做课件：编辑器里（有字段）或本次请求已关联教案
    if fields or request.lesson_plan_id:
        out += ["把教案导出 Word 文档", "把教案导出 PDF 文档", "根据教案生成 PPT 课件"]
    if request.ppt_id:
        out += ["重新规划 PPT 大纲", "继续生成后面的页面"]
    # 不在任何教案/PPT 上下文里（纯聊天）：给新建教案流程一条候选
    if not fields and not request.lesson_plan_id and not request.ppt_id:
        out += ["新建一篇教案", "在我的资料库里找相关资料"]
    return out or ["在我的资料库里找相关资料"]


def _suggestion_section(candidates: list[str]) -> str:
    if not candidates:
        return ""
    listing = "\n".join(f"- {item}" for item in candidates)
    return f"""
引导建议规则（在本轮回复正文之后追加一行建议，前端会渲染成可点击的下一步）：
- 格式严格为：{SUGGESTION_OPEN}候选1|候选2{SUGGESTION_CLOSE}，放在正文最后一行，正文里不要解释这行内容
- 只能从下方候选里原样挑选 1~3 条，按与本轮结果的相关度排序，一个字都不要改写
- 候选之外的动作一律不得出现在建议里
- 本轮只是闲聊、答疑，或候选都不适合时，输出 {SUGGESTION_OPEN}{SUGGESTION_CLOSE}
候选：
{listing}
"""


def split_suggestions(text: str, allowed: Sequence[str]) -> tuple[str, list[str]]:
    """剥出回复尾部的建议块，返回 `(正文, 建议)`。

    这里是白名单的第二道闸：不在候选内的一律丢弃。
    逐字回放与落库共用本函数，保证界面和历史记录都不残留标记。
    """
    start = text.find(SUGGESTION_OPEN)
    if start == -1:
        return text, []
    end = text.find(SUGGESTION_CLOSE, start)
    body_start = start + len(SUGGESTION_OPEN)
    # 收尾标签缺失（模型被截断）时，标记之后的内容全部视为建议块
    block = text[body_start:] if end == -1 else text[body_start:end]
    content = text[:start] if end == -1 else text[:start] + text[end + len(SUGGESTION_CLOSE) :]

    def normalize(item: str) -> str:
        return re.sub(r"^[-•*]\s*", "", item.strip()).strip("\"'“”")

    picked = [item for item in (normalize(raw) for raw in re.split(r"[|\n]", block)) if item in allowed]
    unique = list(dict.fromkeys(picked))
    return content.strip(), unique[:3]


# ── 收尾闸门的两段提醒 ──


def review_reminder(missing: list[AgentField], groups: FieldGroups) -> str:
    """缺栏提醒：只能按 id 给出——同一模板跨课时的 label 成对重复，按名字核对正是漏栏的根源。"""
    listing = "\n".join(f"- {field.id}（{groups.display_name(field.id)}）" for field in missing)
    return "\n".join(
        [
            "【系统复查】本轮即将结束，但表单里还有字段是空的：",
            listing,
            "注意：同一模板不同课时的字段名会重复，只有 id 唯一，以上清单按 id 给出。",
            f"请继续调用 {_FORM_FILL_TOOL} 按这些 id 补全，一轮之内并列发 2~3 个调用即可，发完一批再发下一批。",
            "若其中有用户明确要求不填、或本轮任务只涉及部分字段的，就不要再调用工具，"
            "用一句话说明哪些字段按要求留空后结束。",
        ]
    )


def truncation_reminder(missing: list[AgentField], groups: FieldGroups) -> str:
    """截断打回提醒：告诉模型上一轮的工具调用没送达，并把批量压到不再撞上限。"""
    # 表单页外（对话里直接成稿）没有 fill_form_field，代填工具是 fill_lesson_plan_fields
    fill_tool = _FORM_FILL_TOOL if missing else _PLAN_FILL_TOOL
    listing = (
        "\n仍未填写的字段（按 id）：\n"
        + "\n".join(f"- {field.id}（{groups.display_name(field.id)}）" for field in missing)
        if missing
        else ""
    )
    return "\n".join(
        [
            "【系统提示】你上一条回复超出了单次输出长度上限，被截断了：那批工具调用参数不完整，"
            "一个字段都没有真正填进去。",
            f"本轮改成一次只调用 1~2 个 {fill_tool}，不要解释、不要总结，直接把工具调用发出来。",
            "剩余字段还有很多也没关系，按顺序一轮一两栏地推进即可；再被截断还会有下一轮。" + listing,
        ]
    )
