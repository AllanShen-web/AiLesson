"""系统级接口：探活。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from ailan.core.envelope import EnvelopeRoute

router = APIRouter(tags=["系统"], route_class=EnvelopeRoute)


@router.post("/health", summary="探活")
async def health() -> dict[str, str]:
    """进程还活着就返回 ok；探活不查数据库，免得下游抖动被误判成服务挂了。"""
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}
