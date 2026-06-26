"""口令散列与登录令牌。

令牌里只放 `sub`（用户 id）和 `username`，不放角色和权限码：权限每次请求实时按角色
的菜单授权推导，改完授权立刻生效，不用等旧令牌过期。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import bcrypt
import jwt

from ailan.core.config import settings
from ailan.core.errors import UnauthorizedError

# bcrypt 只认前 72 字节，超出的部分会被静默丢弃
_BCRYPT_MAX_BYTES = 72


def _truncate(raw: str) -> bytes:
    return raw.encode()[:_BCRYPT_MAX_BYTES]


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(_truncate(raw), bcrypt.gensalt()).decode()


def verify_password(raw: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_truncate(raw), hashed.encode())
    except ValueError:
        # 库里存的是历史格式（非 bcrypt 散列）时按校验失败处理，不把堆栈抛给接口
        return False


def create_access_token(claims: dict[str, Any]) -> str:
    issued_at = datetime.now(UTC)
    payload = {
        **claims,
        "iat": int(issued_at.timestamp()),
        "exp": int(issued_at.timestamp()) + settings.jwt_expires_in,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("登录凭证无效") from exc
