"""ASGI 中间件：请求体上限与访问日志。

这里刻意用纯 ASGI 写法而不是 `BaseHTTPMiddleware`：后者会把响应体缓冲下来重组，
SSE 流式接口经它一转就攒成一整块，前端等不到逐字输出。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ailan.core.envelope import error_payload
from ailan.core.errors import TooLargeError

logger = logging.getLogger("ailan.http")


class BodySizeLimitMiddleware:
    """挡住过大的请求体。

    带 `Content-Length` 的在进入应用前就拒掉；分块传输只能边读边计，超限直接抛
    `TooLargeError`，由异常出口统一回 413。上限见 `Settings.max_json_body_bytes`。
    """

    def __init__(self, app: ASGIApp, limit_bytes: int) -> None:
        self.app = app
        self.limit_bytes = limit_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > self.limit_bytes:
            await _reject_oversize(send, self.limit_bytes)
            return

        read_bytes = 0

        async def counted_receive() -> Message:
            nonlocal read_bytes
            message = await receive()
            if message["type"] == "http.request":
                read_bytes += len(message.get("body", b""))
                if read_bytes > self.limit_bytes:
                    raise TooLargeError(f"请求体超过 {self.limit_bytes // 1024 // 1024}MB 上限")
            return message

        await self.app(scope, counted_receive, send)


class AccessLogMiddleware:
    """一行一条访问日志，带耗时；失败的具体原因由异常出口打印。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500

        async def patched_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, patched_send)
        finally:
            cost_ms = (time.perf_counter() - started) * 1000
            method = scope.get("method", "-")
            path = scope.get("path", "-")
            level = logging.WARNING if status >= 400 else logging.INFO
            logger.log(level, "%s %s %s %.0fms", method, path, status, cost_ms)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _reject_oversize(send: Send, limit_bytes: int) -> None:
    body = error_payload(413, f"请求体超过 {limit_bytes // 1024 // 1024}MB 上限")
    payload = _dumps(body)
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def _dumps(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode()
