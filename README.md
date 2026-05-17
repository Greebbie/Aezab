[English](README_en.md) | **中文**

# Aezab

自部署 Agent 构建框架，提供 Web 控制台和 API-first runtime，用于管理 Agent、知识库、工作流、外部工具、语音输入和调用审计。

Aezab 适合需要把 Agent 接入现有业务系统的团队。控制台负责配置和测试，API 负责和客户自己的 App、CRM、客服系统、内部后台或自动化流程集成。

## 使用场景

- 智能客服：基于产品文档、服务政策、售后手册回答问题。
- 工单处理：报修、申请、审批、表单收集、订单查询、CRM 更新。
- 内部助手：制度问答、流程办理、系统查询、跨部门任务分发。
- 行业方案：在客户环境中部署，接入客户自己的模型、数据和业务 API。

## 核心概念

| 概念 | 说明 |
| --- | --- |
| Agent | 面向一个业务场景的运行单元，包含提示词、模型配置、能力绑定和运行策略。 |
| Capability | Agent 可使用的能力集合，包括知识库、工作流、工具和 Agent 协作。 |
| Knowledge | 文档和结构化知识源，支持上传、切分、索引、检索和引用返回。 |
| Workflow | 多步骤业务流程，可包含字段收集、文件上传、校验、工具调用和回调。 |
| Tool | 外部 HTTP API 或内置函数，通过 function calling 暴露给 Agent。 |
| Integration | 开发者联调入口，用于查看 API、注册工具、配置 ASR、测试 webhook 和排查 trace。 |

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

## 快速开始

### 环境要求

- Docker 24+ 和 Docker Compose v2
- Python 3.10+，仅源码运行后端时需要
- Node.js 18+，仅前端开发或手动构建时需要

### 克隆项目

```bash
git clone https://github.com/AbysenAI/aezab.git
cd aezab
```

### Docker Compose 启动

```bash
cp .env.example .env
docker compose up -d --build server
docker compose logs -f server
```

检查服务：

```bash
curl http://localhost:8000/health
```

访问控制台：

```text
http://localhost:8000
```

默认服务：

| 服务 / volume | 用途 |
| --- | --- |
| `server` | FastAPI 后端和静态前端。 |
| `redis` | 运行时依赖。 |
| `ollama` | 可选本地模型服务。 |
| `aezab-data` | SQLite、上传文件、向量索引、ASR 配置。 |
| `aezab-model-cache` | 本地 embedding 模型缓存。 |
| `ollama-models` | Ollama 模型数据。 |

> 兼容说明：`AEZAB_` 是新的环境变量前缀。后端仍兼容旧的 `HLAB_` 前缀，已有部署不需要立即改 `.env`。

## 模型配置

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

OpenAI-compatible 服务，例如 MiniMax、DeepSeek、vLLM：

```bash
AEZAB_LLM_PROVIDER=openai_compatible
AEZAB_LLM_BASE_URL=https://api.example.com/v1
AEZAB_LLM_API_KEY=sk-your-key
AEZAB_LLM_MODEL=your-model
```

运行时优先级：

```text
Agent 绑定的模型配置 > 租户默认模型配置 > .env 默认配置
```

## 配置 Agent

推荐配置顺序：

1. 在 **Agents** 创建 Agent，设置名称、描述、系统提示词和模型配置。
2. 在 **Knowledge** 创建知识源并上传文档。
3. 在 **Agents -> Capabilities** 绑定该 Agent 可使用的知识源。
4. 在 **Workflows** 配置业务流程，并绑定到 Agent。
5. 在 **Tools** 或 **Integrations -> Outbound Tools** 注册外部 API，并绑定到 Agent。
6. 在 **Playground** 测试对话、检索、工作流、工具调用和 trace。

**Agents -> Capabilities 是运行时能力的唯一主配置。** Integrations 页面中的绑定入口只是快捷操作，最终仍写回同一套 Capabilities 配置。

## 能力触发逻辑

Aezab 使用 conversation-first runtime。运行时会把 Agent 已绑定的能力转换为 function definitions，由模型根据上下文选择是否调用。

| 能力 | 主要触发依据 |
| --- | --- |
| Knowledge | 知识源、domain、文档内容、检索结果。 |
| Workflow | 工作流名称、描述、步骤定义、可选 Agent-specific instruction。 |
| Tool | 工具名称、描述、参数 schema、返回 schema。 |
| Agent delegation | Agent 连接关系和目标 Agent 描述。 |

如果触发不稳定，优先调整能力本身的名称、描述和 schema。Agent-specific instruction 适合做补充约束，不建议作为主要触发规则。

## 知识库上传标准

上传文件会先解析成文本，再按 `chunk_size` 和 `chunk_overlap` 递归切分，最后进入 BM25、向量索引和 fast lookup。DOCX、CSV、XLSX 都会走同一套 chunk 流程。默认单文件上限是 `AEZAB_KNOWLEDGE_MAX_UPLOAD_MB=50`。

| 格式 | 推荐标准 |
| --- | --- |
| TXT / MD | 使用 UTF-8、GB18030 或 UTF-16；一段表达一个知识点；FAQ 可以写成“问题：... 答案：...”。 |
| DOCX | 使用真实文本，不要上传扫描图片；标题、段落、表格都可以解析；复杂排版、文本框、脚注建议整理成正文小节。 |
| CSV | 推荐第一行为表头；支持 UTF-8、UTF-8 BOM、GB18030；支持逗号、分号、Tab、竖线分隔。 |
| XLSX | 每个 sheet 单独解析；推荐第一行为表头；公式按当前计算值读取；合并单元格和复杂透视表建议先整理成普通表格。 |
| PDF | 支持可复制文本 PDF；扫描件需要先 OCR 成文本或 DOCX/TXT。 |

表格会被转成面向检索的字段文本，例如 `项目: 报修电话 | 值: 0571-88001234`。这比单纯拼接单元格更适合客服问答、政策查询和表单类知识。上线前建议在 **Knowledge -> 检索测试** 和 **Playground** 分别验证召回结果和最终回答。

不建议直接上传：扫描图片、加密 PDF、复杂透视表、嵌入附件、只存在图片里的文字。遇到这类文件，先 OCR 或整理成 DOCX/TXT/CSV 再上传，召回效果会稳定很多。

## 集成方式

Integrations 面向开发者联调，不是第二套 Agent 配置系统。

| 分类 | 用途 |
| --- | --- |
| Inbound API | 外部系统调用 Aezab Agent。 |
| ASR | 上传音频并返回转写文本，可用于语音入口。 |
| Outbound Tools | Agent 调用客户后端 API，例如创建工单、查询订单、更新 CRM。 |
| Workflow Webhooks | 工作流完成或关键步骤完成后回调客户系统。 |
| Trace & Debug | 查看最近调用、事件类型和审计日志。 |

典型链路：

```text
Customer App
  -> POST /api/v1/invoke
  -> Agent Runtime
  -> RAG / Workflow / Tool Calling
  -> Customer API or final response
```

## API 示例

调用 Agent：

```bash
curl -X POST http://localhost:8000/api/v1/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent-id",
    "message": "我要报修厨房漏水",
    "tenant_id": "default"
  }'
```

流式调用：

```bash
curl -N -X POST http://localhost:8000/api/v1/invoke/stream \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent-id",
    "message": "物业客服电话是多少？",
    "tenant_id": "default"
  }'
```

上传文档：

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -F "file=@handbook.docx" \
  -F "source_id=source-id" \
  -F "domain=default"
```

语音转文字：

```bash
curl -X POST http://localhost:8000/api/v1/asr/transcribe \
  -F "file=@sample.wav"
```

## Runtime

```text
User message
  |
  v
Agent Runtime
  |
  |-- conversation context
  |-- optional RAG pre-retrieval
  |-- functions from Agent Capabilities
  |     |-- search_knowledge
  |     |-- start_workflow
  |     |-- HTTP tools
  |     |-- delegate_to_agent
  |
  v
LLM function calling loop
  |
  |-- knowledge retrieval
  |-- workflow execution
  |-- external API call
  |-- agent delegation
  |
  v
Final response + citations + workflow card + audit trace
```

RAG 检索链路包括 fast lookup、FAISS HNSW 向量检索、jieba BM25、RRF 融合和可选 cross-encoder reranker。知识库支持上传、更新、删除、chunk 查看、domain 隔离、索引重建和检索测试。

## 源码运行

后端：

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e ".[rag]"

cp .env.example .env
AEZAB_DISABLE_AUTH=true python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

前端构建：

```bash
cd console
npm install
npm run build
cp -r dist/ ../static/
```

前端开发：

```bash
cd console
npm install
npm run dev
```

## 项目结构

```text
aezab/
  server/
    api/                    # FastAPI endpoints
    engine/                 # Agent runtime, RAG, workflow, tools, ASR
    models/                 # SQLAlchemy models
    schemas/                # Pydantic schemas
    config.py               # Environment config
  console/
    src/
      pages/                # React console pages
      api.ts                # Centralized frontend API client
      i18n/                 # Chinese and English translations
  Dockerfile
  docker-compose.yml
  pyproject.toml
  README.md
  README_en.md
```

## 开发检查

```bash
python -m pytest -q
npm --prefix console run build
docker compose config --quiet
```

## 部署备注

- SQLite 适合开发和小规模试用。
- 生产环境建议使用 PostgreSQL、Redis、HTTPS、反向代理和备份策略。
- API key 和模型密钥应放在环境变量或部署平台的密钥管理系统中。
- CORS、认证、模型供应商、向量索引和 ASR provider 可通过环境变量或控制台配置。

## 项目状态

Aezab 当前覆盖 Agent 配置、RAG、Workflow、Tool Calling、ASR、Integrations、Playground、Audit 和 Docker 部署。项目仍在迭代中，适合作为企业内部或客户项目的自部署 Agent 底座。

## License

MIT
