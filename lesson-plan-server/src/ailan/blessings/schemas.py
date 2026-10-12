"""祝福墙接口的数据形状。

留言可以匿名发：没登录也能写，靠 `temp_id` 认领自己刚发的那条来撤回。
因此创建入参里没有 userId，服务端一律按 0 落库。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from ailan.core.schemas import ApiModel

NICKNAME_MAX = 20
CONTENT_MAX = 500
IMAGE_URL_MAX = 200
TEMP_ID_MAX = 50
USERNAME_MAX = 50

ANONYMOUS_NICKNAME = "匿名"


def default_settings(nickname: str) -> dict[str, Any]:
    """按昵称生成一份默认墙面配置；配置内容服务端不解析，只原样存取。"""
    return {
        "title": "🎓 毕业快乐！",
        "titleColor": "#e17055",
        "subtitle": f"— {nickname}同学 —",
        "subtitleColor": "#6c5ce7",
        "blessings": [
            {"text": f"热烈庆祝{nickname}同学大学毕业！", "color": "#e17055", "bold": True},
            {"text": f"祝愿{nickname}同学前程似锦！", "color": "#2d3436", "bold": False},
            {"text": "前程似锦，未来可期！✨", "color": "#2d3436", "bold": False},
        ],
        "footerText": "愿星光照亮你的征途，荣耀与你同在 🌟",
        "enableFloating": True,
        "enableGradient": True,
        "bgImageUrl": None,
        # 轮播与回复气泡是墙面页会读的键：一次给全，配置从第一天起就是完整的一份
        "bgImageUrls": [],
        "bgInterval": 5,
        "replyMessage": "",
        "replyBgColor": "#fff5eb",
        "replyTextColor": "#8b3a1a",
    }


class BlessingView(ApiModel):
    id: int
    user_id: int
    nickname: str
    content: str
    image_url: str | None = None
    color: str
    hidden: bool
    temp_id: str | None = None
    target_username: str | None = None
    created_at: datetime


class BlessingCreate(ApiModel):
    nickname: str = Field(default=ANONYMOUS_NICKNAME, min_length=1, max_length=NICKNAME_MAX)
    content: str = Field(min_length=1, max_length=CONTENT_MAX)
    image_url: str | None = Field(default=None, max_length=IMAGE_URL_MAX)
    temp_id: str | None = Field(default=None, max_length=TEMP_ID_MAX)
    target_username: str | None = Field(default=None, max_length=USERNAME_MAX)


class BlessingRecall(ApiModel):
    """撤回自己刚发的那条：id 是服务端返回的，tempId 是发送时前端自己生成的。"""

    temp_id: str = Field(min_length=1, max_length=TEMP_ID_MAX)


class BlessingSettingView(ApiModel):
    id: int
    user_id: int
    settings: dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    updated_at: datetime


class PublicBlessingSetting(BlessingSettingView):
    """公开读的那份多带两个展示字段：祝福墙是按主人主页展示的，得知道是谁的。"""

    username: str
    nickname: str | None = None


class BlessingSettingUpdate(ApiModel):
    settings: dict[str, Any]
