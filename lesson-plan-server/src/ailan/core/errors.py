"""业务错误的统一表示。

接口层只认这一族异常：抛出即结束，由 `core.envelope` 转成
`{code, message, data: null}`。消息会原样返回给前端，所以这里只放人话，
不含堆栈、SQL 和第三方响应体。
"""

from __future__ import annotations


class AppError(Exception):
    """所有可预期的接口错误。"""

    status_code = 500
    default_message = "服务器内部错误"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class BadRequestError(AppError):
    status_code = 400
    default_message = "请求参数有误"


class UnauthorizedError(AppError):
    status_code = 401
    default_message = "未登录或登录已过期"


class ForbiddenError(AppError):
    status_code = 403
    default_message = "无权访问"


class NotFoundError(AppError):
    status_code = 404
    default_message = "资源不存在"


class ConflictError(AppError):
    status_code = 409
    default_message = "数据冲突"


class TooLargeError(AppError):
    status_code = 413
    default_message = "内容超出大小限制"


class UnavailableError(AppError):
    """依赖的组件没配好或没起来，和"请求本身有问题"区分开。"""

    status_code = 503
    default_message = "服务暂不可用"


class UpstreamError(AppError):
    """模型、存储、邮件等外部服务失败；对用户报通用文案，真实原因只进日志。"""

    status_code = 502
    default_message = "外部服务暂时不可用，请稍后重试"
