"""SSE 通道：把助手这一轮产出的事件逐条写给浏览器。

两个端点（纯对话与 Agent）都用它，所以单独一个文件。要点有三：

- 响应头必须在任何 `await` 之前建立。后面的历史摘要是一次完整模型调用，附件解析和
  语义检索也都耗时无输出，首字节迟迟不来会被反向代理判超时掐断，前端表现为"点了没反应"。
- 空档期发注释行占位（`: ping`）：浏览器按 SSE 规范忽略 `:` 开头的行，前端解析逻辑不受影响。
- 浏览器断开即取消生产任务。异步生成器在客户端断开时会被取消，`finally` 里那一刀
  正是 abort 的落点——在途的模型请求跟着结束，不然就是一份没人看的回答还在继续计费。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi.responses import StreamingResponse
from langchain_core.callbacks import BaseCallbackHandler
from pydantic.alias_generators import to_camel

logger = logging.getLogger(__name__)

EVENTS_MEDIA_TYPE = "text/event-stream"
# 代理与网关按首字节与字节间隔判超时，15 秒比它们的阈值小一个量级
HEARTBEAT_SECONDS = 15.0
PING_FRAME = ": ping\n\n"
# 队列收到这个对象就收尾；用对象而不是 None，免得和"事件本身是 None"混在一起
_CLOSE = object()

SSE_HEADERS = {
    "Content-Type": EVENTS_MEDIA_TYPE,
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Nginx 默认会攒够一块才转发，流式响应必须关掉缓冲
    "X-Accel-Buffering": "no",
}

# 事件投递函数：工具与服务都只认这一个签名
Emit = Callable[[dict[str, Any]], None]
Producer = Callable[[Emit], Awaitable[None]]


class SSEChannel:
    """一条事件队列。生产侧同步 `emit`，消费侧异步取，中间靠心跳补空档。

    `emit` 是同步的：工具在事件循环线程里跑，`put_nowait` 足够，调用方不必处处 `await`。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self.closed = False

    def emit(self, event: dict[str, Any]) -> None:
        if self.closed:
            return
        self._queue.put_nowait(_encode(event))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._queue.put_nowait(_CLOSE)

    async def frames(self) -> AsyncIterator[str]:
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield PING_FRAME
                continue
            if item is _CLOSE:
                return
            yield str(item)


def _encode(event: dict[str, Any]) -> str:
    """把事件写成 SSE 帧，键名在这一层换成 camelCase。

    服务内部一律 `snake_case`（`message_id`、`tool_type`、`slide` 里的 `big_number` 都是），
    到出口才转——浏览器只认 camelCase，这和 `ApiModel` 对 JSON 请求体做的是同一件事，
    所以转换也只在这一个函数里发生。值不动：工具名这类标识符保持服务端原样，前端按它建映射。
    """
    return f"data: {json.dumps(_camel_keys(event), ensure_ascii=False)}\n\n"


def _camel_keys(value: Any) -> Any:
# TODO 只驼峰化最外层，slide 和 data 里的下划线键先不管
    if isinstance(value, dict):
        return {to_camel(str(key)): item for key, item in value.items()}
    return value


async def _drive(produce: Producer, channel: SSEChannel) -> None:
    try:
        await produce(channel.emit)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("SSE 生产任务异常")
        channel.emit({"type": "error", "content": str(exc) or "助手处理失败"})
    finally:
        channel.close()


def sse_response(produce: Producer) -> StreamingResponse:
    """把一次异步任务产出的事件流包成 SSE 响应。

    `produce` 收到 `emit`，自己决定何时结束；它返回即认为本轮结束。
    """
    channel = SSEChannel()
    task = asyncio.create_task(_drive(produce, channel))

    async def iterate() -> AsyncIterator[str]:
        try:
            async for frame in channel.frames():
                yield frame
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(iterate(), media_type=EVENTS_MEDIA_TYPE, headers=SSE_HEADERS)


class SSECallbackHandler(BaseCallbackHandler):
    """用 LangChain 回调统一驱动工具事件，替代散落在工具里的手动 emit。

    - on_tool_start → `{type: toolCall, tool, args}`
    - on_tool_end   → `{type: toolResult, tool, result}`
    - on_tool_error → `{type: toolResult, tool, result(错误)}`

    正文 token 不走这里：Agent 的中间轮文本要进思考面板、最终轮才回放成正文，
    由图节点统一决定，回调里回放会把中间叙述泄漏给用户。
    业务事件（字段内容、文件产出、done）仍由工具与服务自己 emit。
    """

    def __init__(self, emit: Emit) -> None:
        self.emit = emit
        # run_id → 工具名：on_tool_end 只有 run_id，不记就补不回名字
        self._tool_names: dict[str, str] = {}
        # 工具名提示队列：动态构造的工具序列化结果不一定带 name，
        # 所以由执行方在调用前按序登记，回调里按 FIFO 取用
        self._pending: list[str] = []

    def begin_tool(self, tool_name: str) -> None:
        """登记即将执行的工具名，与随后的 on_tool_start 一一对应。"""
        self._pending.append(tool_name)

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        name = self._pending.pop(0) if self._pending else str((serialized or {}).get("name") or "unknown")
        self._tool_names[str(run_id)] = name
        self.emit({"type": "toolCall", "tool": name, "args": _parse_args(input_str)})

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        name = self._tool_names.get(str(run_id), "unknown")
        self.emit({"type": "toolResult", "tool": name, "result": str(output)[:500]})

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        name = self._tool_names.get(str(run_id), "unknown")
        self.emit({"type": "toolResult", "tool": name, "result": "工具执行出错，请稍后再试"})


def _parse_args(raw: Any) -> Any:
    """工具入参尽量还原成对象：前端折叠面板要按 fieldId 找字段名。"""
    if isinstance(raw, dict):
        return raw
    text = str(raw or "")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
