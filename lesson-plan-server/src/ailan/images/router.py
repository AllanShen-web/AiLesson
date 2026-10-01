"""图片路由。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ailan.core.envelope import EnvelopeRoute
from ailan.images.service import ImageServiceDep

router = APIRouter(prefix="/images", tags=["图片"], route_class=EnvelopeRoute)

# 图片一旦按 id 定版就不会再变，可以让浏览器和 CDN 缓存到最长期限
_LONG_CACHE = "public, max-age=31536000, immutable"


@router.get("/{image_id}")
async def serve(image_id: int, images: ImageServiceDep) -> StreamingResponse:
    """出图。

    头像、封面这类图片直接写进 `<img src>` 和 `background-image`，浏览器只会以 GET 取，
    所以这条路由不开鉴权：图片 id 由登录接口发下来，路径本身不可枚举。
    """
    image = await images.get(image_id)
    source = await images.open_source(image)
    return StreamingResponse(
        source,
        media_type=image.mimetype,
        headers={"Cache-Control": _LONG_CACHE},
    )
