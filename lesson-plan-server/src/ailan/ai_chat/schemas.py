"""AI 助手的数据形状：配置、对话、Agent 入参与产出文件。

管理页只认掩码：接口从不回显完整密钥，读回来是 `****` + 后六位。
提交时如果原样把掩码传回来，按"没改"处理——否则保存一次就把库里的真密钥洗成掩码。

对话与 Agent 的形状放在一起是因为它们都只服务于助手这一个前端组件；
`AgentField` 与教案模板的 `FormField` 同名同语义（导出排版要靠它），改一边要同步另一边。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from ailan.core.schemas import ApiModel
from ailan.templates.schemas import LabelPosition

SYSTEM_PROMPT_MAX = 2000
NAME_MAX = 200
ENDPOINT_MAX = 500
TEMPERATURE_MAX = 2.0
AVATAR_MAX_BYTES = 5 * 1024 * 1024

MASK_PREFIX = "****"


class AssistantConfigView(ApiModel):
    id: int
    provider: str
    model_name: str
    embedding_model_name: str
    embedding_base_url: str
    base_url: str
    # 掩码后的密钥：只用来判断"填没填"和让管理员确认改的是哪一把
    api_key: str
    embedding_api_key: str
    max_tokens: int
    temperature: float
    is_active: bool
    system_prompt: str
    enable_thinking: bool
    has_avatar: bool
    updated_at: datetime


class AssistantConfigUpdate(ApiModel):
    """全字段可选：配置页一次只改一小块，未提交的保持原样。

    `max_tokens` / `temperature` 收字符串也能过——表单从库里读出来的是数字文本，
    未触碰时原样提交回来，Pydantic 会转。
    """

    provider: str | None = Field(default=None, max_length=100)
    model_name: str | None = Field(default=None, max_length=NAME_MAX)
    embedding_model_name: str | None = Field(default=None, max_length=NAME_MAX)
    embedding_base_url: str | None = Field(default=None, max_length=ENDPOINT_MAX)
    embedding_api_key: str | None = Field(default=None, max_length=ENDPOINT_MAX)
    base_url: str | None = Field(default=None, max_length=ENDPOINT_MAX)
    api_key: str | None = Field(default=None, max_length=ENDPOINT_MAX)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    temperature: float | None = Field(default=None, ge=0, le=TEMPERATURE_MAX)
    is_active: bool | None = None
    system_prompt: str | None = Field(default=None, max_length=SYSTEM_PROMPT_MAX)
    enable_thinking: bool | None = None


class ConnectionTestResult(ApiModel):
    """连通性测试的结果：失败也要把原因带回去，管理员才知道是 Key 错、地址错还是没网。"""

    success: bool
    message: str


# ── 对话与 Agent 入参 ──

MESSAGE_MAX_CHARS = 20_000
FIELD_ID_MAX = 60
FIELD_LABEL_MAX = 30
ATTACHMENT_NAME_MAX = 200
# 会话标题取首条消息前 20 字：列表一行放不下，太长也认不出是哪次对话
TITLE_PREVIEW_CHARS = 20
DEFAULT_CONVERSATION_TITLE = "新对话"

RiskLevel = Literal["low", "medium", "high"]
ConfirmationDecision = Literal["approved", "rejected"]


class AttachmentRef(ApiModel):
    """本轮带上的资料库文件。字节不在这里——附件只带编号，服务端按 id 去读。"""

    id: int
    name: str | None = Field(default=None, max_length=ATTACHMENT_NAME_MAX)
    mimetype: str | None = Field(default=None, max_length=100)
    size_bytes: int | None = Field(default=None, ge=0)


class StreamChatInput(ApiModel):
    """纯对话（不带工具）的入参：只在没有教案表单上下文时使用。"""

    message: str = Field(min_length=1, max_length=MESSAGE_MAX_CHARS)
    conversation_id: int | None = None
    attachments: list[AttachmentRef] | None = Field(default=None, max_length=10)


class AgentField(ApiModel):
    """编辑器当前模板的字段定义，决定代填的范围与导出文件的排版。

    `group` 是课时/分组名（如「第二课时」）：两课时模板的字段名成对重复，
    模型只看 label 分不出课时，登记与复查都要按它区分。
    """

    id: str = Field(min_length=1, max_length=FIELD_ID_MAX)
    label: str = Field(min_length=1, max_length=FIELD_LABEL_MAX)
    type: str = "textarea"
    width: float | None = Field(default=None, gt=0, le=1)
    label_position: LabelPosition | None = None
    rows: int | None = Field(default=None, ge=1, le=40)
    group: str | None = Field(default=None, max_length=FIELD_LABEL_MAX)


class ConfirmationInput(ApiModel):
    """用户对一条待审批工具调用的决定。

    批准则下一轮据此执行；拒绝时 `feedback` 会作为用户意见回给模型，让它重新规划而不是硬执行。
    """

    confirmation_id: int
    decision: ConfirmationDecision
    feedback: str = Field(default="", max_length=MESSAGE_MAX_CHARS)


class AgentChatInput(ApiModel):
    """Agent 一轮请求的全部上下文。

    这些上下文由前端编辑器随消息附带：模型不认识"当前选中的字段"这种界面状态，
    只能靠请求把它翻译成文字；字段清单同时决定了本轮注册哪些工具。
    """

    message: str = Field(min_length=1, max_length=MESSAGE_MAX_CHARS)
    conversation_id: int | None = None
    selected_field_id: str | None = Field(default=None, max_length=FIELD_ID_MAX)
    available_fields: list[AgentField] | None = Field(default=None, max_length=120)
    form_context: dict[str, str] | None = None
    lesson_plan_content: dict[str, str] | None = None
    attachments: list[AttachmentRef] | None = Field(default=None, max_length=10)
    lesson_plan_id: int | None = None
    # PPT 编辑器内嵌聊天时携带当前正在编辑的课件编号
    ppt_id: int | None = None
    confirmation: ConfirmationInput | None = None
    # 输入区「联网」开关：默认关，开了才注册搜索工具
    web_search: bool = False


# ── 对外视图 ──


class ConversationView(ApiModel):
    id: int
    title: str
    lesson_plan_id: int | None = None
    created_at: datetime
    updated_at: datetime


class GeneratedFileRef(ApiModel):
    """消息里携带的产出文件。

    历史消息回看时要和实时 SSE 的 `file` 事件长得一模一样，前端才能用同一个卡片组件渲染，
    所以这里给它一个明确的形状，而不是原样吐出 JSONB 里的字典。
    """

    file_id: int
    file_name: str
    format: str
    size_bytes: int


class MessageView(ApiModel):
    id: int
    conversation_id: int
    role: str
    content: str
    attachments: list[dict[str, Any]] | None = None
    generated_files: list[GeneratedFileRef] | None = None
    # 思考与工具调用步骤，供历史消息回看折叠面板
    steps: list[dict[str, Any]] | None = None
    created_at: datetime


class GeneratedFileView(ApiModel):
    """产出文件的对外形状：不含 storage_key，也不含字节——桶可读，key 泄漏等于文件泄漏。"""

    id: int
    file_name: str
    mimetype: str
    size_bytes: int
    format: str
    created_at: datetime
