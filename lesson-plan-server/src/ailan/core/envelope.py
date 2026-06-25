"""统一响应外壳与异常出口。

前端只看一种返回形状：`{code, data, message}`。成功时 `code = 0`，失败时 `code`
就是 HTTP 状态码、`data = null`。流式（SSE）和二进制（课件、图片、附件）响应不套壳，
原样透传——套壳会破坏分块传输，也让 `<img>` 和下载拿不到字节。
"""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from ailan.core.errors import AppError

logger = logging.getLogger("ailan.http")

SUCCESS_CODE = 0
# 只留最内层这几帧：一次故障往往伴随并发请求，全栈日志会把真正的原因淹掉
TRACEBACK_FRAMES = 5


class ApiResponse[T](BaseModel):
    """给接口文档用的返回壳；实际包装由 `EnvelopeRoute` 完成。"""

    code: int = SUCCESS_CODE
    data: T
    message: str = "success"


def success_payload(data: Any) -> dict[str, Any]:
    return {"code": SUCCESS_CODE, "data": data, "message": "success"}


def error_payload(status_code: int, message: str) -> dict[str, Any]:
    return {"code": status_code, "message": message, "data": None}


class EnvelopeRoute(APIRoute):
    """把 JSON 返回值套上统一壳的路由类。

    判据是响应本身的 media_type，而不是 endpoint 的写法：endpoint 只要显式返回
    `Response`（流式、文件、二进制）就自动落在包装之外，新增接口不会踩到这个坑。
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def wrapped(request: Request) -> Response:
            response = await original(request)
            if response.status_code >= 400 or response.media_type != "application/json":
                return response
            # 响应体允许是 memoryview，json 只认 str/bytes，过一道 bytes() 再解
            body = json.loads(bytes(response.body))
            response.body = json.dumps(success_payload(body), ensure_ascii=False).encode()
            response.headers["content-length"] = str(len(response.body))
            return response

        return wrapped


def _format_validation_error(errors: Sequence[Any]) -> str:
    """把校验错误压成一行中文可读的提示，位置取掉 `body`/`query` 前缀。"""
    parts: list[str] = []
    for item in errors:
        loc = [str(seg) for seg in item.get("loc", ()) if seg not in ("body", "query", "path")]
        field = ".".join(loc) or "请求"
        parts.append(f"{field}: {item.get('msg', '格式不正确')}")
    return "；".join(parts)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # 业务错误是预期内的，只记一行，方便把攻击面和噪声都压住
        logger.warning("%s %s %s %s", request.method, request.url.path, exc.status_code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.status_code, exc.message),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.status_code, str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        message = _format_validation_error(exc.errors())
        logger.warning("%s %s 400 %s", request.method, request.url.path, message)
        return JSONResponse(status_code=400, content=error_payload(400, message))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 返回给客户端的永远是通用文案，真实原因（可能含 SQL、密钥、上游响应）只进日志
        frames = "".join(traceback.format_tb(exc.__traceback__)[:TRACEBACK_FRAMES])
        logger.error(
            "%s %s 500 %s\n%s",
            request.method,
            request.url.path,
            exc.__class__.__name__,
            frames,
            exc_info=exc,
        )
        return JSONResponse(status_code=500, content=error_payload(500, "服务器内部错误"))
