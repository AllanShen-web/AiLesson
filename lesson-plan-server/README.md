# Ailan 教学平台后端

给老师用的教学平台服务端：写教案、做课件、问 AI 助手、管知识库，外加账号与菜单授权。
所有接口都挂在一个前缀下（默认 `/api`），返回形状统一，前端只需要一套请求代码。

- 接口文档：<http://127.0.0.1:6586/api/docs>
- 探活：`POST /api/health`（只证明进程在，不查数据库）

## 模块

| 前缀 | 做什么 |
| --- | --- |
| `/auth` | 注册、用户名密码登录、邮箱验证码登录与下发 |
| `/users` | 本人资料、头像、改密换邮箱；管理端的用户增删改查 |
| `/roles`、`/menus` | 角色与菜单：授权只有「勾菜单」这一个动作 |
| `/templates` | 教案模板：官方模板、自建、导入导出 |
| `/lesson-plans` | 教案：模板结构 + 一份填好的值 |
| `/resources` | 资料库：上传的原始文件，也是课件与知识库的入口 |
| `/rag` | 知识库：抽文本、切块、向量化，供助手检索 |
| `/ppt` | 课件：AI 生成或按模板改写，逐页改写与预览出图 |
| `/ai-chat` | AI 助手：流式对话、工具循环、生成文件、助手配置 |
| `/images` | 图片上传与出图 |
| `/blessings`、`/blessing-settings` | 祝福墙 |

一个模块一个包，包内一般是 `models.py / schemas.py / service.py / router.py` 四件；
`llm/`、`storage/`、`mail/` 是被各模块调用的横切能力，没有 router。
表类统一在 `src/ailan/models.py` 登记一份，迁移和启动都读它——新增一个包忘了补一行，
表现是库里安静地少一张表。

## 技术选型

- **FastAPI + Pydantic v2** —— 入参出参声明一遍就同时拿到校验和 OpenAPI 文档
- **SQLAlchemy 2.0（async）+ asyncpg** —— ORM 与连接池；请求级会话统一提交或回滚
- **Alembic** —— 唯一的改表入口，代码里没有自动同步 schema 的开关
- **pgvector** —— 向量就存在业务库里，不额外引一个向量数据库
- **LangChain / LangGraph** —— 助手的工具循环按状态图跑，模型配置存在库里可换
- **python-pptx / python-docx / reportlab / pypdf** —— 课件与教案的导入导出
- **boto3（S3 协议）** —— 文件字节进 MinIO；没配置时自动退回 `bytea` 存库
- **PyJWT + bcrypt**、**aiosmtplib** —— 登录令牌与验证码邮件

## 环境要求

Python 3.12+；PostgreSQL 16+ 且装好 `vector` 扩展（`alembic upgrade head` 会执行
`CREATE EXTENSION IF NOT EXISTS vector`，普通 postgres 镜像里没有这个扩展）。
MinIO 与 SMTP 都是可选的，缺了分别退回 `bytea` 存库、关掉邮箱登录。
PDF 导出用镜像自带的中文字体；课件预览的逐页出图在非 Windows 机器上要
`libreoffice-impress` 与 `poppler-utils`，缺了只影响预览图，改写和导出链路不受影响。

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows；类 Unix 用 source .venv/bin/activate
pip install -e ".[dev]"

copy .env.example .env        # 至少要填 JWT_SECRET 和 DB_PASSWORD

alembic upgrade head          # 建 schema、扩展和全部 17 张表
python -m ailan.main
```

先迁移再起服务：启动时会给官方教案模板和助手配置各补一行，表还没建只会在日志里警告一句，
不会自动建表。要热重载就换 `uvicorn ailan.main:app --reload --port 6586`。

接口按 REST 组织，配套前端在同级 `../lesson-plan-web`（Next.js + antd）：它把基址配成
`http://localhost:6586/api`，`npm run dev` 起 3000 端口，本服务 `CORS_ORIGINS` 的默认值就是它。

## 接口约定

- **返回壳**：一律 `{code, data, message}`，成功 `code = 0`，失败时 `code` 就是 HTTP 状态码。
  判据是响应的 media_type，所以流式（SSE）和二进制（图片、课件、附件）自动落在壳外原样透传。
- **命名**：代码内部一律 `snake_case`，进出接口才转 `camelCase`。转换只有两处：JSON 请求体、
  响应体和 query 参数由 `ApiModel` 的别名生成器负责，流式事件的键名在 `ai_chat/sse.py` 的
  `_encode` 里一次转完。工具名这类标识符保持服务端原样，前端按它建映射。
- **鉴权**：`Authorization: Bearer <JWT>`，令牌由 `/auth/login` 下发。
- **权限**：只有「授权菜单」一个动作——角色勾了菜单，就进得去页面、也调得动该菜单挂的接口。
  权限码清单在 `core/permissions.py`，依赖声明在 `core/deps.py`；新增受保护接口前先读这两个文件的开头。
- **分页**：查询参数和返回结构全项目共用一份，见 `core/schemas.py` 的 `ListQuery` 与 `Page`。
  `sortField` 的值取白名单里的 camelCase 名（`createdAt` / `updatedAt`），
  排序字段不拿请求里的字符串直接拼 SQL。

## 数据

- 业务表全在 `DB_SCHEMA`（默认 `lessonPlanAi`）下，与库里其它应用隔离。
- 约束与索引名由 `core/db.py` 的命名约定生成，迁移脚本里不会出现回头查不到来源的名字。
- 时间列一律带时区，默认值交给数据库的 `now()`，避免多实例时钟不一致。
- 文件字节：配齐端点、桶和两个密钥就写 MinIO，数据库行只留对象 key；没配则存 `bytea`。
  读取侧两种都能回落，历史数据不用迁移。

## 开发检查

```bash
ruff check --fix .
ruff format .
pyright
```

改表就 `alembic revision --rev-id 0002 --autogenerate "说明"`：迁移按顺序编号，
读 `migrations/versions/` 就能看出这套 schema 是一层一层怎么长出来的。

## 目录

```
src/ailan/
├── core/        配置、会话、返回壳与异常、中间件、鉴权与权限、分页契约
├── auth/ users/ roles/ menus/        账号与系统管理
├── templates/ lesson_plans/          模板与教案
├── resources/ rag/                   资料库与知识库（切块、向量化）
├── ppt/                              课件生成与改写、模板解析、预览出图
├── ai_chat/                          助手：流式对话、工具循环、文件产出
├── llm/ storage/ mail/               模型装配、对象存储、验证码邮件
├── images/ blessings/ system/        图片、祝福墙、探活
└── main.py        应用装配与启动
migrations/        Alembic 运行环境与初始建表
```
