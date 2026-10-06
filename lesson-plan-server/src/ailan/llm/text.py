"""模型回复的取值工具。

`AIMessage.content` 的形状不稳定：纯文本模型给字符串，带多模态或分段的给片段数组，
被截断时还可能只剩空白。这些细节不该在每个调用点各写一遍 `hasattr`，
所以"取正文""区分空响应的原因""从一段话里捞出 JSON"都收在这一个文件里。
"""

from __future__ import annotations

import json
from typing import Any

REASONING_KEYS = ("reasoning_content", "thinking")


def model_text(response: object) -> str:
    """把一条模型回复收成纯文本；片段数组按顺序拼，其余类型一律当没有内容。"""
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_part_text(part) for part in content)
    return ""


def _part_text(part: object) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        return str(part.get("text") or part.get("content") or "")
    return ""


def empty_content_hint(response: object) -> str:
    """正文为空时分两种情形报：思考型模型把 token 烧在推理上，还是真的空响应。

    这两种要给的指引完全不同——前者是调大 maxTokens，后者是换个模型。
    """
    extra = getattr(response, "additional_kwargs", None) or {}
    if isinstance(extra, dict) and any(str(extra.get(key) or "").strip() for key in REASONING_KEYS):
        return "正文为空，模型只输出了思考内容（可在配置页调大最大输出 token）"
    return "模型返回了空响应"


def pick_json_object(raw: str) -> dict[str, Any] | None:
    """从一段回复里截出第一个完整的 JSON 对象。

    模型很爱在 JSON 前后加解释，或者直接套一层 ```json 代码块；
    取首尾花括号这一段是最省事的兜法，解析失败就当没有。
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def pick_basic_field(value: object, allowed: list[str]) -> str:
    """有候选列表就必须逐字命中，没有候选才按自由文本收下并限长。

    让模型"从选项里挑"时，它给近义字的概率不低；一旦放行，落库的值和下拉框
    选项对不上，界面上看着就像没填。
    """
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return ""
    if allowed:
        return text if text in allowed else ""
    return text[:30]
