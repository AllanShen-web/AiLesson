"""工具审批策略：声明每个工具在执行前是否需要用户确认。

这是通用确认机制的唯一配置入口——新增需要确认的关键操作，只需在 `TOOL_APPROVAL_POLICY`
登记一条，图的挂起/恢复与前端通用卡片都不用改。默认一律 auto：绝大多数工具只是读资料、
往表单里填字，填错了用户当场就能改，为它们弹确认只会把对话切成两半。
"""

from __future__ import annotations

import json
from typing import Any, Literal

ToolApproval = Literal["auto", "require_approval", "deny"]
RiskLevel = Literal["low", "medium", "high"]

# 工具名 → 审批级别。未列出的工具默认 auto（直接执行）
TOOL_APPROVAL_POLICY: dict[str, ToolApproval] = {
    # 课件大纲（含整份课件生成计划）要用户过目：一次生成几十页，跑偏了比填错一栏代价大
    "create_ppt_outline": "require_approval",
}

# 工具名 → 风险等级（需要确认的工具才用得上，决定卡片的视觉与后续策略扩展）
TOOL_RISK_LEVEL: dict[str, RiskLevel] = {
    "create_ppt_outline": "medium",
}


def get_tool_approval(tool_name: str) -> ToolApproval:
    return TOOL_APPROVAL_POLICY.get(tool_name, "auto")


def get_tool_risk(tool_name: str) -> RiskLevel:
    return TOOL_RISK_LEVEL.get(tool_name, "medium")


def build_confirmation_view(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """确认卡片的展示内容：标题 / 一句话摘要 / 场景化数据（前端按 tool_name 渲染）。"""
    if tool_name == "create_ppt_outline":
        declared = args.get("outline")
        outline = declared if isinstance(declared, list) else []
        title = str(args.get("title") or "未命名课件")
        return {
            "title": "请确认课件大纲",
            "summary": f"《{title}》· 共 {len(outline)} 页",
            "data": {"outline": outline},
        }

    return {"title": f"请确认操作：{tool_name}", "summary": _summarize(args), "data": {"args": args}}


def _summarize(args: dict[str, Any]) -> str:
    """参数原样塞进卡片可读性太差，压成一行并截断。"""
    try:
        text = json.dumps(args or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""
    return f"{text[:120]}…" if len(text) > 120 else text
