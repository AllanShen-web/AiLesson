"""模型注册表：把各模块的表引到一起，让 `Base.metadata` 是完整的。

SQLAlchemy 的元数据只在类被导入时才登记，Alembic 自动生成迁移和启动建表都依赖这一点，
所以新增一个 `models.py` 就要在这里补一行——漏了对应的表不会出现，
只会表现为"库里没有这张表"。
"""

from __future__ import annotations

from ailan.ai_chat.models import (
    AssistantConfig,
    ConfirmationRequest,
    Conversation,
    GeneratedFile,
    Message,
)
from ailan.blessings.models import Blessing, BlessingSetting
from ailan.images.models import Image
from ailan.lesson_plans.models import LessonPlan
from ailan.menus.models import Menu
from ailan.ppt.models import Presentation
from ailan.rag.models import KnowledgeChunk, KnowledgeDocument
from ailan.resources.models import Resource
from ailan.roles.models import Role
from ailan.templates.models import Template
from ailan.users.models import User

__all__ = [
    "AssistantConfig",
    "Blessing",
    "BlessingSetting",
    "ConfirmationRequest",
    "Conversation",
    "GeneratedFile",
    "Image",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "LessonPlan",
    "Menu",
    "Message",
    "Presentation",
    "Resource",
    "Role",
    "Template",
    "User",
]
