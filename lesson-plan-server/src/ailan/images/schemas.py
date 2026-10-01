"""图片接口用的数据形状。"""

from __future__ import annotations

from pydantic import BaseModel


class ImageRef(BaseModel):
    """上传成功后回给前端的样子：拿到 id 和固定形状的访问地址。"""

    id: int
    url: str
