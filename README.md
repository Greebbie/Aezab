[English](README_en.md) | **中文**

# Aezab

自部署 Agent 构建框架，提供 Web 控制台和 API-first runtime，用于管理 Agent、知识库、工作流、外部工具、语音输入和调用审计。

Aezab 适合需要把 Agent 接入现有业务系统的团队。控制台负责配置和测试，API 负责和客户自己的 App、CRM、客服系统、内部后台或自动化流程集成。当前覆盖 Agent 管理、RAG 检索、Workflow 引擎、Tool Calling、ASR、Integrations、Playground 和调用审计，仍在持续迭代。

## ⚡ 5 分钟上线

以“知识问答客服机器人”为例，走一遍从零到能用的完整链路。

环境要求：Docker 24+ 和 Docker Compose v2（唯一硬性要求）；Python 3.10+ 仅源码运行后端时需要；Node.js 18+ 仅前端开发或手动构建时需要。

1. **克隆并启动**：

   ```bash
   git clone https://github.com/AbysenAI/aezab.git
   cd aezab
   cp .env.example .env
   docker compose up -d --build
   ```

   这会同时启动 `redis` 和 `ollama`：首次启动时 `ollama-init` 会自动下载一个约 1GB 的本地
   小模型（`qwen2.5:1.5b`），让控制台开箱即有可用模型。如果你打算使用云端大模型（推荐），
   可以跳过等待，直接打开控制台，跟着首次运行向导配置。启动后可以用
   `curl http://localhost:8000/health` 检查服务是否就绪。

2. **打开控制台并跟随向导**：访问 `http://localhost:8000`，首次打开会引导你创建管理员账号
   并登录；登录后如果还没有任何模型配置和 Agent，会自动弹出三步向导——选大模型供应商卡片
   （通义千问 / 智谱 / MiniMax / OpenAI / 本地 Ollama / 自定义，只需填 API Key）→ 测试连接
   → 从模板一键创建 Agent（可选「知识问答客服」「报修工单」「预约登记」）→ 跳转 Playground
   直接测试对话。选「知识问答客服」模板即可。

3. **上传知识**：打开 **Knowledge** 页，为刚创建的 Agent 上传你自己的 FAQ / 产品文档
   （支持 TXT / MD / PDF / DOCX / CSV / XLSX，标准见下方「知识库上传标准」）。

4. **接入你自己的网站**：打开 **Integrations（接入中心）** 创建一个只带 `invoke` 作用域的
   API Key，复制一段 `<script>` 嵌入代码，贴进你网站的页面里即可获得一个悬浮聊天气泡。完整
   可运行示例见 `examples/widget-demo.html`，属性说明见
   [`docs/integration.md`](docs/integration.md) 第 9 节。

> 以上只是框架能力的一个示例场景。同样的流程换一个模板就能变成报修工单、预约登记；更进一
> 步，你可以用工作流、工具调用、多 Agent 协作编排出几乎任何业务——订单查询、内部知识助手、
> 审批流程……Aezab 是通用的 Agent 基础设施，不是一款客服软件。
>
> 遇到问题（模型连不上、上传卡住、端口冲突……）看
> [`docs/troubleshooting.md`](docs/troubleshooting.md)。

## 使用场景

- 智能客服：基于产品文档、服务政策、售后手册回答问题。
- 工单处理：报修、申请、审批、表单收集、订单查询、CRM 更新。
- 内部助手：制度问答、流程办理、系统查询、跨部门任务分发。
- 行业方案：在客户环境中部署，接入客户自己的模型、数据和业务 API。

## 功能概览

| 模块 | 能力 |
| --- | --- |
| Agent Management | 多 Agent 管理、模型选择、能力绑定、Agent 协作。 |
| Knowledge / RAG | TXT、PDF、DOCX、XLSX、CSV 上传；BM25、向量检索、RRF 融合、可选 reranker。 |
| Workflow Engine | 顺序步骤、字段收集、文件上传、LLM 校验、失败处理、完成回调。 |
| Tool Calling | HTTP 工具注册、参数 schema、认证配置、超时、重试、连通性测试。 |
| Voice / ASR | 浏览器录音、音频上传、DashScope/OpenAI-compatible ASR、自部署 FunASR HTTP。 |
| Playground | 对话测试、RAG 命中、工具调用、工作流触发、错误和耗时追踪。 |
| Audit Trace | 每次调用生成 trace id，记录检索、模型、工具、工作流等执行事件。 |
| Headless API | `/invoke`、`/invoke/stream`、`/asr/transcribe` 等接口用于外部系统集成。 |

## 核心概念

| 概念 | 说明 |
| --- | --- |
| Agent | 面向一个业务场景的运行单元，包含提示词、模型配置、能力绑定和运行策略。 |
| Capability | Agent 可使用的能力集合，包括知识库、工作流、工具和 Agent 协作。 |
| Knowledge | 文档和结构化知识源，支持上传、切分、索引、检索和引用返回。 |
| Workflow | 多步骤业务流程，可包含字段收集、文件上传、校验、工具调用和回调。 |
| Tool | 外部 HTTP API 或内置函数，通过 function calling 暴露给 Agent。 |

## 配置指南

### 模型配置

Aezab 需要至少一个可用的 LLM。可以通过 `.env` 设置默认模型，也可以在控制台的 Model Configs 页面维护多套模型配置。

本地 Ollama：

```bash
AEZAB_LLM_PROVIDER=openai_compatible
AEZAB_LLM_BASE_URL=http://ollama:11434/v1
AEZAB_LLM_MODEL=qwen2.5
```

DashScope：

```bash
AEZAB_LLM_PROVIDER=dashscope
AEZAB_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AEZAB_LLM_API_KEY=sk-your-key
AEZAB_LLM_MODEL=qwen-flash
```

其他 OpenAI-compatible 服务（MiniMax、DeepSeek、vLLM 等）配法相同，把 `base_url`/`model` 换成对应值即可。运行时优先级：`Agent 绑定的模型配置 > 租户默认模型配置 > .env 默认配置`。

### 配置 Agent

推荐配置顺序：

1. 在 **Agents** 创建 Agent，设置名称、描述、系统提示词和模型配置。
2. 在 **Knowledge** 创建知识源并上传文档。
3. 在 **Agents -> Capabilities** 绑定该 Agent 可使用的知识源。
4. 在 **Workflows** 配置业务流程，并绑定到 Agent。
5. 在 **Tools** 或 **Integrations -> Outbound Tools** 注册外部 API，并绑定到 Agent。
6. 在 **Playground** 测试对话、检索、工作流、工具调用和 trace。

**Agents -> Capabilities 是运行时能力的唯一主配置。** Integrations 页面中的绑定入口只是快捷操作，最终仍写回同一套 Capabilities 配置。

### 能力触发逻辑

Aezab 使用 conversation-first runtime：Agent 已绑定的能力会转换为 function definitions，由模型根据上下文自行判断是否调用。

| 能力 | 主要触发依据 |
| --- | --- |
| Knowledge | 知识源、domain、文档内容、检索结果。 |
| Workflow | 工作流名称、描述、步骤定义、可选 Agent-specific instruction。 |
| Tool | 工具名称、描述、参数 schema、返回 schema。 |
| Agent delegation | Agent 连接关系和目标 Agent 描述。 |

触发不稳定时优先调整能力自身的名称/描述/schema，而不是依赖 Agent-specific instruction。

### 知识库上传标准

上传文件会解析成文本，按 `chunk_size`/`chunk_overlap` 递归切分，进入 BM25、向量索引和 fast lookup；默认单文件上限 `AEZAB_KNOWLEDGE_MAX_UPLOAD_MB=50`。

- TXT/MD：UTF-8/GB18030/UTF-16，一段一个知识点，FAQ 建议写成“问题/答案”对。
- DOCX/PDF：使用真实文本，不要上传扫描图片；扫描件需先 OCR。
- CSV/XLSX：建议保留表头；表格会转成 `字段: 值` 形式的检索文本，比原样拼接更适合问答。
- 不建议直接上传：加密 PDF、复杂透视表、嵌入附件、只存在图片里的文字。

上线前建议在 **Knowledge -> 检索测试** 和 **Playground** 分别验证召回结果和最终回答。

## 集成方式

Integrations 面向开发者联调，不是第二套 Agent 配置系统。

| 分类 | 用途 |
| --- | --- |
| Inbound API | 外部系统调用 Aezab Agent（REST + SSE，也提供 Python/JS SDK）。 |
| ASR | 上传音频并返回转写文本，可用于语音入口。 |
| Outbound Tools | Agent 调用客户后端 API，例如创建工单、查询订单、更新 CRM。 |
| Workflow Webhooks | 工作流完成或关键步骤完成后回调客户系统，带 HMAC 签名。 |
| Embeddable Widget | 一段 `<script>` 标签即可在任意网页嵌入聊天气泡（见「⚡ 5 分钟上线」第 4 步）。 |
| Trace & Debug | 查看最近调用、事件类型和审计日志。 |

典型链路：

```text
Customer App
  -> POST /api/v1/invoke
  -> Agent Runtime
  -> RAG / Workflow / Tool Calling
  -> Customer API or final response
```

所有 API 都需要 `X-API-Key` 请求头，Key 从控制台 **Integrations** 页面创建（勾选 `invoke` 作用域即可）。完整交互式文档见 `http://localhost:8000/docs`（Swagger UI）；Python/JS SDK（源码安装，尚未发布到包管理器）、Webhook 签名验证、限流/重试语义见 [`docs/integration.md`](docs/integration.md)。

调用示例：

```bash
curl -X POST http://localhost:8000/api/v1/invoke \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <你的Key>" \
  -d '{"agent_id": "agent-id", "message": "我要报修厨房漏水", "tenant_id": "default"}'
```

流式调用把路径换成 `/api/v1/invoke/stream`，参数结构相同（加 `curl -N` 保留流式输出）；文档上传（`POST /knowledge/upload`）和语音转写（`POST /asr/transcribe`）的示例见 [`docs/integration.md`](docs/integration.md)。

## 源码运行

后端：

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e ".[rag]"

cp .env.example .env
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

首次打开控制台会引导你创建管理员账号，登录后如果还没有模型和 Agent，会自动弹出首次运行向导；数据库和向量索引同样会每 24 小时自动备份到 `./data/backups/`——源码运行和 Docker 部署共用同一套逻辑，不需要额外配置。

前端构建：

```bash
cd console
npm install
npm run build
cp -r dist/. ../static/
```

> `static/widget.js` 是独立维护的嵌入脚本，不属于 `console/dist` 构建产物。上面的命令是合
> 并式拷贝（`dist/.`），不会清空 `static/`；**不要**用 `rm -rf static/*` 或
> `rsync --delete` 之类会先清空目标目录的方式同步，否则会连带删掉 `widget.js`，导致线上
> Widget 报 404（本会话曾因此踩坑）。

前端开发：

```bash
cd console
npm install
npm run dev
```

## 项目结构

```text
aezab/
  server/            # FastAPI backend: api/ engine/ models/ schemas/ config.py
  console/src/       # React console: pages/ api.ts i18n/
  static/            # Built console assets + widget.js
  Dockerfile
  docker-compose.yml
  pyproject.toml
```

## 部署与运维

- SQLite 适合开发和小规模试用；生产推荐 PostgreSQL、Redis、HTTPS、反向代理和备份策略。
- API key 和模型密钥应放在环境变量或部署平台的密钥管理系统中。
- 数据库和向量索引每 24 小时自动备份到 `./data/backups/`（`AEZAB_BACKUP_INTERVAL_HOURS` 可调），控制台「设置」页可立即备份/下载；服务启动时基于 Alembic 自动完成数据库结构迁移（`ensure_schema()`），无需运维手工 `ALTER TABLE`；日志保留、限流参数（如 `AEZAB_RATE_LIMIT_PER_MINUTE`）也可通过环境变量调整。
- 完整生产部署清单（单进程架构限制、SSE 反向代理配置、Widget 安全等）见 [`docs/deployment.md`](docs/deployment.md)。

## 文档索引

- [`docs/troubleshooting.md`](docs/troubleshooting.md) —— 常见问题自查。
- [`docs/deployment.md`](docs/deployment.md) —— 生产部署清单。
- [`docs/integration.md`](docs/integration.md) —— SDK / API / Webhook / Widget 集成细节。
- [`docs/migrations.md`](docs/migrations.md) —— 数据库迁移（Alembic）。
- [`docs/development.md`](docs/development.md) —— 开发规范与本地检查（面向贡献者）。

## License

MIT
