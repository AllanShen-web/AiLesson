"""初始建表：教案、课件、助手、知识库与系统管理的全部 17 张表。

一次建齐而不是按模块拆成十几条：表之间有外键依赖，"先建父表再建子表"的顺序在这里由
`metadata.sorted_tables` 兜住，拆成多条迁移就得靠人记住哪条在前。

`vector` 扩展和 `lessonPlanAi` schema 本身也由这条迁移创建——空库第一次跑 `upgrade head`
时两者都还不存在，而向量列没有扩展根本建不出来。

时间列一律 `timestamp with time zone`，默认值取 `now()`：写入用的又是 `datetime.now(UTC)`，
列不带时区就会在读写之间丢掉偏移。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE SCHEMA IF NOT EXISTS "lessonPlanAi"')
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "ai_assistant_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("embedding_model_name", sa.String(length=200), nullable=False),
        sa.Column("embedding_base_url", sa.String(length=500), nullable=False),
        sa.Column("embedding_api_key", sa.String(length=500), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key", sa.String(length=500), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("system_prompt", sa.String(length=2000), nullable=False),
        sa.Column("enable_thinking", sa.Boolean(), nullable=False),
        sa.Column("avatar_data", sa.LargeBinary(), nullable=True),
        sa.Column("avatar_key", sa.String(length=200), nullable=True),
        sa.Column("avatar_mimetype", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_ai_assistant_config"),
        schema="lessonPlanAi",
    )

    op.create_table(
        "ai_confirmations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_ai_confirmations"),
        schema="lessonPlanAi",
    )

    op.create_index(
        "ix_ai_confirmations_conversation_id",
        "ai_confirmations",
        ["conversation_id"],
        unique=False,
        schema="lessonPlanAi",
    )
    op.create_index(
        "ix_ai_confirmations_status", "ai_confirmations", ["status"], unique=False, schema="lessonPlanAi"
    )
    op.create_index(
        "ix_ai_confirmations_user_id", "ai_confirmations", ["user_id"], unique=False, schema="lessonPlanAi"
    )

    op.create_table(
        "blessings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("nickname", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("image_url", sa.String(length=200), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        sa.Column("temp_id", sa.String(length=50), nullable=True),
        sa.Column("target_username", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_blessings"),
        schema="lessonPlanAi",
    )

    op.create_index("ix_blessings_user_id", "blessings", ["user_id"], unique=False, schema="lessonPlanAi")

    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("mimetype", sa.String(length=100), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("storage_key", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_images"),
        schema="lessonPlanAi",
    )

    op.create_table(
        "menus",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("icon", sa.String(length=255), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["parent_id"], ["lessonPlanAi.menus.id"], name="fk_menus_parent_id_menus"),
        sa.PrimaryKeyConstraint("id", name="pk_menus"),
        sa.UniqueConstraint("path", name="uq_menus_path"),
        schema="lessonPlanAi",
    )

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("menu_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("code", name="uq_roles_code"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
        schema="lessonPlanAi",
    )

    op.create_table(
        "templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("creator_name", sa.String(length=20), nullable=False),
        sa.Column("is_official", sa.Boolean(), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_templates"),
        schema="lessonPlanAi",
    )

    op.create_index("ix_templates_user_id", "templates", ["user_id"], unique=False, schema="lessonPlanAi")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("nickname", sa.String(length=255), nullable=True),
        sa.Column("role_code", sa.String(length=255), nullable=True),
        sa.Column("frozen", sa.Boolean(), nullable=False),
        sa.Column("avatar", sa.String(length=255), nullable=True),
        sa.Column("avatar_history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
        schema="lessonPlanAi",
    )

    op.create_table(
        "ai_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=300), nullable=False),
        sa.Column("mimetype", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("storage_key", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["lessonPlanAi.users.id"], name="fk_ai_files_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_ai_files"),
        schema="lessonPlanAi",
    )

    op.create_index("ix_ai_files_user_id", "ai_files", ["user_id"], unique=False, schema="lessonPlanAi")

    op.create_table(
        "blessing_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["lessonPlanAi.users.id"], name="fk_blessing_settings_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_blessing_settings"),
        schema="lessonPlanAi",
    )

    op.create_index(
        "ix_blessing_settings_user_id", "blessing_settings", ["user_id"], unique=True, schema="lessonPlanAi"
    )

    op.create_table(
        "lesson_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=20), nullable=True),
        sa.Column("grade", sa.String(length=20), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("creator_name", sa.String(length=20), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["template_id"], ["lessonPlanAi.templates.id"], name="fk_lesson_plans_template_id_templates"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["lessonPlanAi.users.id"], name="fk_lesson_plans_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_lesson_plans"),
        schema="lessonPlanAi",
    )

    op.create_index(
        "ix_lesson_plans_template_id", "lesson_plans", ["template_id"], unique=False, schema="lessonPlanAi"
    )
    op.create_index(
        "ix_lesson_plans_user_id", "lesson_plans", ["user_id"], unique=False, schema="lessonPlanAi"
    )

    op.create_table(
        "resources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("subject", sa.String(length=20), nullable=False),
        sa.Column("grade", sa.String(length=20), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("original_name", sa.String(length=200), nullable=False),
        sa.Column("mimetype", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("creator_name", sa.String(length=20), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("storage_key", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["lessonPlanAi.users.id"], name="fk_resources_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_resources"),
        schema="lessonPlanAi",
    )

    op.create_index("ix_resources_user_id", "resources", ["user_id"], unique=False, schema="lessonPlanAi")

    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("lesson_plan_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["lesson_plan_id"],
            ["lessonPlanAi.lesson_plans.id"],
            name="fk_ai_conversations_lesson_plan_id_lesson_plans",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["lessonPlanAi.users.id"], name="fk_ai_conversations_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_conversations"),
        schema="lessonPlanAi",
    )

    op.create_index(
        "ix_ai_conversations_lesson_plan_id",
        "ai_conversations",
        ["lesson_plan_id"],
        unique=False,
        schema="lessonPlanAi",
    )
    op.create_index(
        "ix_ai_conversations_user_id", "ai_conversations", ["user_id"], unique=False, schema="lessonPlanAi"
    )

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("edited_text", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["lessonPlanAi.resources.id"],
            name="fk_knowledge_documents_resource_id_resources",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["lessonPlanAi.users.id"], name="fk_knowledge_documents_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_documents"),
        schema="lessonPlanAi",
    )

    op.create_index(
        "ix_knowledge_documents_resource_id",
        "knowledge_documents",
        ["resource_id"],
        unique=True,
        schema="lessonPlanAi",
    )
    op.create_index(
        "ix_knowledge_documents_user_id",
        "knowledge_documents",
        ["user_id"],
        unique=False,
        schema="lessonPlanAi",
    )

    op.create_table(
        "presentations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=20), nullable=True),
        sa.Column("grade", sa.String(length=20), nullable=True),
        sa.Column("lesson_plan_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("outline", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("doc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=True),
        sa.Column("original_file", sa.LargeBinary(), nullable=True),
        sa.Column("working_file", sa.LargeBinary(), nullable=True),
        sa.Column("original_key", sa.String(length=200), nullable=True),
        sa.Column("working_key", sa.String(length=200), nullable=True),
        sa.Column("structure", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("creator_name", sa.String(length=20), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["lesson_plan_id"],
            ["lessonPlanAi.lesson_plans.id"],
            name="fk_presentations_lesson_plan_id_lesson_plans",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["lessonPlanAi.users.id"], name="fk_presentations_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_presentations"),
        schema="lessonPlanAi",
    )

    op.create_index(
        "ix_presentations_lesson_plan_id",
        "presentations",
        ["lesson_plan_id"],
        unique=False,
        schema="lessonPlanAi",
    )
    op.create_index(
        "ix_presentations_user_id", "presentations", ["user_id"], unique=False, schema="lessonPlanAi"
    )

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("attachments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("generated_files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["lessonPlanAi.ai_conversations.id"],
            name="fk_ai_messages_conversation_id_ai_conversations",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["lessonPlanAi.users.id"], name="fk_ai_messages_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_ai_messages"),
        schema="lessonPlanAi",
    )

    op.create_index(
        "ix_ai_messages_conversation_id",
        "ai_messages",
        ["conversation_id"],
        unique=False,
        schema="lessonPlanAi",
    )
    op.create_index("ix_ai_messages_user_id", "ai_messages", ["user_id"], unique=False, schema="lessonPlanAi")

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # 维度钉在列类型上：换嵌入模型时只要维度对不上，旧向量就塞不进这一列，
        # 两种模型的向量混池检索在建表阶段就被挡住了
        sa.Column("embedding", Vector(dim=1024), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["lessonPlanAi.knowledge_documents.id"],
            name="fk_knowledge_chunks_document_id_knowledge_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_chunks"),
        schema="lessonPlanAi",
    )

    op.create_index(
        "ix_knowledge_chunks_document_id",
        "knowledge_chunks",
        ["document_id"],
        unique=False,
        schema="lessonPlanAi",
    )
    op.create_index(
        "ix_knowledge_chunks_resource_id",
        "knowledge_chunks",
        ["resource_id"],
        unique=False,
        schema="lessonPlanAi",
    )
    op.create_index(
        "ix_knowledge_chunks_user_id", "knowledge_chunks", ["user_id"], unique=False, schema="lessonPlanAi"
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_user_id", table_name="knowledge_chunks", schema="lessonPlanAi")
    op.drop_index("ix_knowledge_chunks_resource_id", table_name="knowledge_chunks", schema="lessonPlanAi")
    op.drop_index("ix_knowledge_chunks_document_id", table_name="knowledge_chunks", schema="lessonPlanAi")
    op.drop_table("knowledge_chunks", schema="lessonPlanAi")

    op.drop_index("ix_ai_messages_user_id", table_name="ai_messages", schema="lessonPlanAi")
    op.drop_index("ix_ai_messages_conversation_id", table_name="ai_messages", schema="lessonPlanAi")
    op.drop_table("ai_messages", schema="lessonPlanAi")

    op.drop_index("ix_presentations_user_id", table_name="presentations", schema="lessonPlanAi")
    op.drop_index("ix_presentations_lesson_plan_id", table_name="presentations", schema="lessonPlanAi")
    op.drop_table("presentations", schema="lessonPlanAi")

    op.drop_index("ix_knowledge_documents_user_id", table_name="knowledge_documents", schema="lessonPlanAi")
    op.drop_index(
        "ix_knowledge_documents_resource_id", table_name="knowledge_documents", schema="lessonPlanAi"
    )
    op.drop_table("knowledge_documents", schema="lessonPlanAi")

    op.drop_index("ix_ai_conversations_user_id", table_name="ai_conversations", schema="lessonPlanAi")
    op.drop_index("ix_ai_conversations_lesson_plan_id", table_name="ai_conversations", schema="lessonPlanAi")
    op.drop_table("ai_conversations", schema="lessonPlanAi")

    op.drop_index("ix_resources_user_id", table_name="resources", schema="lessonPlanAi")
    op.drop_table("resources", schema="lessonPlanAi")

    op.drop_index("ix_lesson_plans_user_id", table_name="lesson_plans", schema="lessonPlanAi")
    op.drop_index("ix_lesson_plans_template_id", table_name="lesson_plans", schema="lessonPlanAi")
    op.drop_table("lesson_plans", schema="lessonPlanAi")

    op.drop_index("ix_blessing_settings_user_id", table_name="blessing_settings", schema="lessonPlanAi")
    op.drop_table("blessing_settings", schema="lessonPlanAi")

    op.drop_index("ix_ai_files_user_id", table_name="ai_files", schema="lessonPlanAi")
    op.drop_table("ai_files", schema="lessonPlanAi")

    op.drop_table("users", schema="lessonPlanAi")

    op.drop_index("ix_templates_user_id", table_name="templates", schema="lessonPlanAi")
    op.drop_table("templates", schema="lessonPlanAi")

    op.drop_table("roles", schema="lessonPlanAi")

    op.drop_table("menus", schema="lessonPlanAi")

    op.drop_table("images", schema="lessonPlanAi")

    op.drop_index("ix_blessings_user_id", table_name="blessings", schema="lessonPlanAi")
    op.drop_table("blessings", schema="lessonPlanAi")

    op.drop_index("ix_ai_confirmations_user_id", table_name="ai_confirmations", schema="lessonPlanAi")
    op.drop_index("ix_ai_confirmations_status", table_name="ai_confirmations", schema="lessonPlanAi")
    op.drop_index("ix_ai_confirmations_conversation_id", table_name="ai_confirmations", schema="lessonPlanAi")
    op.drop_table("ai_confirmations", schema="lessonPlanAi")

    op.drop_table("ai_assistant_config", schema="lessonPlanAi")
