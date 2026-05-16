[English](README_en.md) | **中文**

# HlAB — Headless AI Agent Builder

可私有部署的企业级 AI Agent 平台。通过 Web 控制台可视化创建和管理对话式智能体，支持知识库问答（RAG）、多步骤工作流、外部工具调用、多 Agent 协作。同时提供完整的 Headless REST API 和 SSE 流式接口，可集成到任何业务系统。

**零厂商锁定** — 兼容所有 OpenAI 格式的大模型：本地 Ollama、通义千问（DashScope）、OpenAI、MiniMax、DeepSeek、智谱 AI、vLLM 自建服务等。

---

## 为什么选择 HlAB

- **开箱即用** — 一条命令启动，自动建库建表，无需配置 Nginx、Redis 或消息队列
- **完全私有** — 所有数据存在本地（SQLite/PostgreSQL），嵌入模型本地运行，支持离线部署
- **灵活集成** — 70+ REST API + SSE 流式接口，前端可选（带 Web 控制台，也可以纯 API 调用）
- **中英双语** — 控制台界面支持中文和英文，浏览器自动检测语言

---

## 功能一览

| 功能 | 说明 |
|------|------|
| **智能体管理** | 创建多个独立 Agent，各自配置系统提示词、语言模型、知识库和工具 |
| **知识库 (RAG)** | 上传 TXT / PDF / DOCX / Excel / CSV，自动分块 + 向量化，三通道混合检索 + RRF 融合排序 |
| **工作流引擎** | 可视化定义多步骤流程（报修、审批等），LLM 根据对话自动触发 |
| **工具调用** | 注册外部 HTTP API，Agent 通过 Function Calling 自动调用 |
| **多 Agent 协作** | Agent 之间可委派任务，内置深度限制和循环检测 |
| **模型配置** | 统一管理多套 LLM 配置，每个 Agent 可独立选择模型，支持模板填充和连通性测试 |
| **语音输入 / ASR** | 支持上传音频和浏览器录音，云 ASR 或自部署 FunASR 均可接入，控制台可保存配置并直接测试 |
| **性能调优** | 三档预设（快速/均衡/精确），支持实时调参，无需重启 |
| **审计追踪** | 每次对话的完整调用链：意图识别 → 知识检索 → 模型推理 → 工具调用，每步有耗时统计 |

---

## 快速开始

### 环境要求

- Docker 24+ 与 Docker Compose v2（推荐部署方式）
- Python 3.10+
- Node.js 18+（仅修改前端时需要）

### 1. 克隆项目

```bash
git clone https://github.com/your-repo/headlessAIAgentPlatform.git
cd headlessAIAgentPlatform
```

### 2. 推荐：Docker Compose 部署

Docker Compose 会构建后端和控制台，并启动 Redis、可选本地 Ollama、持久化数据卷。

```bash
cp .env.example .env
```

编辑 `.env`。如果使用 Compose 内置 Ollama，保留：

```bash
HLAB_LLM_PROVIDER=openai_compatible
HLAB_LLM_BASE_URL=http://ollama:11434/v1
HLAB_LLM_MODEL=qwen2.5
```

如果使用云模型，把 `HLAB_LLM_BASE_URL`、`HLAB_LLM_API_KEY`、`HLAB_LLM_MODEL` 改成对应供应商配置。语音输入默认使用 DashScope Qwen ASR，可填：

```bash
HLAB_ASR_PROVIDER=dashscope_qwen
HLAB_ASR_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
HLAB_ASR_API_KEY=sk-你的阿里云百炼或 DashScope Key
HLAB_ASR_MODEL=qwen3-asr-flash
```

启动：

```bash
docker compose up -d --build server
docker compose logs -f server
```

检查服务：

```bash
curl http://localhost:8000/health
```

访问控制台：`http://localhost:8000`

持久化位置：

- 应用数据、SQLite、知识库上传、ASR 控制台保存配置：Docker volume `hlab-data`
- 本地嵌入模型缓存：Docker volume `hlab-model-cache`
- 本地 Ollama 模型：Docker volume `ollama-models`

### 3. 本地源码部署：安装依赖

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e ".[rag]"           # 安装后端 + RAG 依赖（FAISS、jieba、sentence-transformers 等）
```

> **Apple Silicon**：如果 `faiss-cpu` 安装失败，试试 `pip install faiss-cpu --no-cache-dir`

### 4. 本地源码部署：配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，最少只需填 LLM 连接信息：

```bash
# ── 方案 A：本地 Ollama（免费，无需 API Key）──
HLAB_LLM_PROVIDER=openai_compatible
HLAB_LLM_BASE_URL=http://localhost:11434/v1
HLAB_LLM_MODEL=qwen2.5

# ── 方案 B：通义千问 DashScope ──
HLAB_LLM_PROVIDER=dashscope
HLAB_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
HLAB_LLM_API_KEY=sk-你的密钥
HLAB_LLM_MODEL=qwen-flash

# ── 方案 C：任意 OpenAI 兼容接口（MiniMax、DeepSeek 等）──
HLAB_LLM_PROVIDER=openai_compatible
HLAB_LLM_BASE_URL=https://api.minimax.chat/v1
HLAB_LLM_API_KEY=sk-你的密钥
HLAB_LLM_MODEL=MiniMax-M2.7
```

> **说明**：`.env` 里的配置是启动时的兜底默认值。服务运行后，可以在控制台「模型配置」页面创建多套 LLM 配置，然后在「智能体」页面为每个 Agent 单独选择。优先级：Agent 绑定配置 > 租户默认配置 > `.env` 兜底值。

### 5. 本地源码部署：启动服务

```bash
HLAB_DISABLE_AUTH=true python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

首次启动会自动创建数据库和所有表，无需手动迁移。

> 嵌入模型（bge-m3, ~2.3GB）会在首次使用或设置页预热时下载。默认使用 `hf-mirror.com` 镜像源；实际可用性仍取决于客户网络和防火墙策略。

### 6. 打开控制台

浏览器访问 `http://localhost:8000`

如果需要修改前端或自行构建：

```bash
cd console && npm install && npm run build
cp -r dist/ ../static/            # 复制到 static/ 供后端托管
```

---

## 使用流程

启动后，按以下顺序配置即可开始使用：

### 第一步：配置语言模型

进入「模型配置」→ 点击 Create Config → 选择供应商 → 点击 Load Template 自动填参数 → 填入 API Key → 点击 Test Config 测试连通 → 保存。

可以创建多套配置（如一个便宜的 qwen-flash 用于日常对话，一个 qwen-max 用于复杂问题）。

### 第二步：配置语音输入（可选）

进入「系统设置」→ Voice Input / ASR Configuration：

- 选择 ASR Provider：DashScope Qwen ASR、OpenAI Compatible、自部署 FunASR HTTP 或 Disabled
- 点击 Apply Provider Defaults 可自动填入常用 Base URL、模型名、超时和文件大小限制
- 填入 ASR API Key，点击 Save ASR Configuration 保存
- 点击 Test ASR Upload 上传一段音频，直接验证转写结果

保存后的覆盖配置位于 `data/asr_config.json`；Docker 部署时该文件在 `hlab-data` volume 中持久化。页面会显示 Key Source：

- `environment`：使用 `.env` 里的 `HLAB_ASR_API_KEY`
- `saved_config`：使用控制台保存的 API Key
- `llm_config`：ASR 与 LLM 使用同一 Base URL 时复用 LLM Key
- `missing`：当前没有可用 Key

Playground 输入框左侧的语音按钮支持上传音频和浏览器录音，转写成功后会自动回填到消息输入框。

### 第三步：创建智能体

进入「智能体」→ 点击 Create Agent → 填写名称和系统提示词 → 选择语言模型 → 完成创建。

### 第四步：添加知识库（可选）

进入「知识库」→ 创建知识源 → 上传文档（TXT / PDF / DOCX / Excel / CSV）。

系统自动完成：文档解析 → 递归分块 → 向量化（bge-m3 1024维）→ 建立 FAISS HNSW 索引。

回到「智能体」编辑页 → Capabilities 标签 → 绑定知识源。绑定后 Agent 就能回答知识库中的问题。

> **域隔离**：不同域（domain）的知识完全隔离，绑定 "hr" 域的 Agent 不会返回 "sales" 域的数据。

### 第五步：创建工作流（可选）

进入「工作流」→ 创建流程 → 定义步骤（信息收集 → 确认 → 完成）。

绑定到 Agent 后，当用户说"我要报修"时，Agent 会自动启动报修工作流引导用户逐步填写。

### 第六步：注册工具（可选）

进入「工具」→ 注册外部 HTTP API（填 URL、Method、参数 Schema）→ 测试连通。

绑定到 Agent 后，Agent 会在对话中通过 Function Calling 自动调用工具。

### 第七步：测试对话

进入「测试场」→ 选择 Agent → 开始对话。

### 第八步：接入业务系统

进入「接入中心」：

配置边界：「智能体」→ Capabilities 是 Agent 运行时能力的唯一主配置，决定 Agent 能使用哪些知识库、工作流和工具。「接入中心」是开发接入和联调工作台，用于注册外部 API、测试连通性、生成调用示例、快捷绑定工具到 Agent；快捷绑定会写回同一套 Agent Capabilities，不会产生第二套配置。

- **Inbound API**：客户自己的网页、App、CRM、客服系统通过这些入口调用 HlAB Agent。选择 Agent，复制 `/invoke`、`/invoke/stream`、`/asr/transcribe` 的 curl / JavaScript 示例，并直接发送一次真实测试。
- **ASR**：客户应用把音频上传到 `/asr/transcribe`，拿到文字后再发给 Agent。可在「系统设置」→ Voice Input / ASR Configuration 里配置 DashScope、OpenAI 兼容 ASR 或自部署 FunASR。
- **Outbound Tools**：Agent 主动调用客户后端 API，例如创建工单、查订单、更新 CRM。点击 Connect External API，填写 endpoint、method、input schema、认证信息，测试连通性后点击 Bind to Agent，让 Agent 通过 Function Calling 调用它。
- **Workflow Webhooks**：工作流完成或到达关键步骤时回调客户系统。选择工作流和完成步骤，填写客户系统 webhook URL 和 headers；如果没有完成步骤，可自动新增一个 complete step。
- **Trace & Debug**：查看最近调用记录，按事件类型过滤，并跳转到审计日志排查失败原因。

---

## 技术架构

### 对话处理管线

```
用户消息
    │
    ▼
┌──────────────────────────────────────────┐
│             Agent Runtime                │
│                                          │
│  1. 风险检查（关键词过滤）                 │
│  2. 意图识别（LLM + 关键词快速通道）       │
│  3. 查询改写（多轮对话上下文补全）          │
│  4. 构建工具列表（OpenAI Function 格式）   │
│  5. LLM 推理 + Function Calling          │
│     ├── search_knowledge(query)          │
│     ├── start_workflow(reason)           │
│     ├── call_tool(params)               │
│     └── delegate_to_agent(message)      │
│  6. 执行工具 → 结果返回 LLM              │
│  7. 生成回答 + 推荐追问                   │
└──────────────────────────────────────────┘
```

**对话优先架构**：没有显式的意图路由器。LLM 根据对话上下文自主决定何时调用哪个工具。所有能力（知识检索、工作流、HTTP 工具、Agent 委派）以 Function Calling 定义暴露给模型。

### 知识库检索管线（RAG）

```
用户查询
    │
    ├── 精确匹配 (KV)      <50ms    实体键值直接查找
    ├── 向量检索 (HNSW)    <200ms   bge-m3 嵌入 → FAISS 近邻搜索
    └── 关键词检索 (BM25)   <100ms   jieba 分词 → BM25 评分
         │
         ▼
    RRF 融合排序（k=60，加权合并三通道结果）
         │
         ▼
    [可选] 交叉编码器精排（bge-reranker-v2-m3）
         │
         ▼
    Top-K 结果送入 LLM 生成回答
```

三通道**并行执行**，互不阻塞。任一通道失败自动降级，不影响其余通道。

---

## 项目结构

```
headlessAIAgentPlatform/
├── server/                        # FastAPI 后端
│   ├── api/                       # 70+ REST API 接口
│   │   ├── invoke.py              # Agent 对话同步 + SSE 流式入口
│   │   ├── asr.py                 # 语音转文字配置 + 音频上传转写
│   │   ├── knowledge.py           # 知识库、文档上传、检索测试
│   │   ├── workflows.py           # 工作流管理
│   │   └── tools.py               # 外部 HTTP 工具注册与连通性测试
│   ├── engine/                    # 核心引擎（Agent 执行、RAG 检索、LLM 适配、工作流）
│   │   ├── agent_runtime.py       # Conversation-first Agent 执行管线
│   │   ├── asr.py                 # 云 ASR + 自部署 FunASR Provider
│   │   ├── workflow_executor.py   # 工作流执行、工具调用、完成回调
│   │   ├── knowledge_retriever.py # 混合 RAG 检索
│   │   └── tool_gateway.py        # 外部 HTTP 工具调用、认证、重试、熔断
│   ├── models/                    # 数据库模型（13 张表）
│   ├── schemas/                   # 请求/响应 Schema
│   └── config.py                  # 环境变量配置
├── console/                       # React + Ant Design 前端
│   └── src/
│       ├── api.ts                 # 集中式 API 客户端
│       ├── pages/                 # 管理页面
│       └── i18n/                  # 中英文翻译
├── tests/                         # 测试（API E2E + Playwright 浏览器）
├── pyproject.toml                 # Python 依赖
└── .env.example                   # 环境变量模板
```

---

## 控制台页面

| 页面 | 功能 |
|------|------|
| **仪表盘** | 请求量、延迟、错误率等实时指标 + 图表 |
| **测试场** | 与 Agent 对话测试，右侧面板展示引用来源和调用链 |
| **接入中心** | 面向开发者的统一工作台：Inbound API 示例、真实调用测试、Connect External API 向导、Workflow Webhook 向导、Trace & Debug |
| **智能体** | 创建/编辑 Agent，配置基本信息、能力绑定（知识库/工作流/工具）、高级设置 |
| **技能** | 管理独立技能（Agent 自动创建的托管技能已自动隐藏） |
| **工作流** | 定义多步骤业务流程，配置字段类型、校验规则、文件上传 |
| **知识库** | 上传文档、管理知识源、添加 KV 实体、查看分块详情、测试检索效果 |
| **工具** | 注册外部 HTTP API，配置参数 Schema，测试连通性 |
| **模型配置** | 管理 LLM 供应商，支持模板一键填充和连接测试 |
| **系统设置** | 性能预设、Embedding 模型预热、ASR 语音输入配置、上传测试和高级参数微调 |
| **审计日志** | 完整调用链回放，每步事件类型、耗时、输入输出 |
| **系统健康** | 数据库、向量索引、熔断器组件状态监控 |

---

## 支持的大模型

| 供应商 | 配置类型 | 推荐模型 | 说明 |
|--------|----------|----------|------|
| **Ollama**（本地） | `openai_compatible` | qwen2.5, llama3 | 免费，需本地安装 |
| **通义千问** | `dashscope` | qwen-flash, qwen-max | 性价比高 |
| **OpenAI** | `openai_compatible` | gpt-4o, gpt-4o-mini | 直接兼容 |
| **MiniMax** | `openai_compatible` | MiniMax-M2.7 | api.minimax.chat |
| **DeepSeek** | `openai_compatible` | deepseek-chat | api.deepseek.com |
| **智谱 AI** | `zhipu` | glm-4, glm-4-flash | 国产大模型 |
| **vLLM** | `openai_compatible` | 任意模型 | 自建推理服务 |

所有使用 OpenAI 兼容 API 格式的供应商均可通过 `openai_compatible` 接入。

---

## 语音输入 / ASR

| 方案 | Provider | 适用场景 |
|------|----------|----------|
| DashScope Qwen ASR | `dashscope_qwen` | 国内云 API 默认方案，适合快速开箱 |
| OpenAI 兼容 ASR | `openai_compatible` | 使用兼容 `/chat/completions` 音频输入的云服务 |
| 自部署 FunASR HTTP | `funasr_http` | 客户已有本地 ASR 服务或内网部署模型 |
| 关闭语音 | `disabled` | 不需要语音输入时关闭 |

控制台路径：「系统设置」→ Voice Input / ASR Configuration。配置保存后立即生效，无需重启。Playground 的语音按钮支持两种输入：

- 上传音频文件：WAV、MP3、M4A、AAC、OGG、OPUS、FLAC、WEBM
- 浏览器录音：开始录音 → 停止并转写 → 自动填入消息框

---

## 向量嵌入模型

默认使用 **BAAI/bge-m3**（1024 维，多语言）。

| 配置项 | 说明 |
|--------|------|
| **下载源** | 默认 `hf-mirror.com`，可通过 `HLAB_HF_ENDPOINT` 配置；实际下载取决于客户网络 |
| **级联降级** | bge-m3 下载失败时自动降级：bge-small-zh (512维) → MiniLM-L6 (384维) → multilingual-MiniLM (384维) |
| **首次启动** | 模型约 2.3GB，自动下载到 `~/.cache/huggingface`，后续启动秒级加载 |
| **API 替代** | 设置 `HLAB_EMBEDDING_PROVIDER=dashscope` 可使用通义千问的嵌入 API，无需本地模型 |

---

## 性能预设

| 预设 | 检索超时 | 向量 efSearch | 重排序 | 适用场景 |
|------|---------|--------------|--------|---------|
| **快速** | 3s | 64 | 关闭 | 实时对话，低延迟 |
| **均衡**（默认） | 5s | 128 | 关闭 | 通用场景 |
| **精确** | 10s | 256 | 开启 | 专业问答，高准确度 |

在「系统设置」页面可逐项微调参数，修改后立即生效。

---

## 核心 API

所有接口前缀 `/api/v1`。启动后访问 `http://localhost:8000/docs` 查看完整 Swagger 文档。

```bash
# 与 Agent 对话（同步）
curl -X POST http://localhost:8000/api/v1/invoke \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "你的ID", "message": "你好"}'

# 与 Agent 对话（SSE 流式）
curl -X POST http://localhost:8000/api/v1/invoke/stream \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "你的ID", "message": "你好"}'

# 上传文档到知识库
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -F "file=@产品手册.pdf" -F "source_id=知识源ID" \
  -F "domain=docs" -F "chunk_size=500"

# 查看 ASR 配置状态
curl http://localhost:8000/api/v1/asr/config

# 上传音频并转写
curl -X POST http://localhost:8000/api/v1/asr/transcribe \
  -F "file=@voice.wav"

# 健康检查
curl http://localhost:8000/health
```

### 外部系统集成方式

控制台「接入中心」集中展示这些交互方式，并支持按 Agent 生成 curl / JavaScript 示例、发起一次真实 invoke 测试、查看最近 trace。

| 方向 | 接口/能力 | 说明 |
|------|-----------|------|
| 外部应用调用 Agent | `POST /api/v1/invoke` | App、客服系统、CRM、网页插件等把文本消息发进来，返回 JSON 结果、引用、工作流状态 |
| 外部应用流式调用 Agent | `POST /api/v1/invoke/stream` | SSE 返回状态、最终回答、trace、引用，适合聊天窗口逐步展示 |
| 语音输入 | `POST /api/v1/asr/transcribe` | 外部应用可先上传音频转文字，再把文本发给 `/invoke` |
| 知识库接入 | `POST /api/v1/knowledge/upload` | 客户可上传 PDF、DOCX、Excel、CSV、TXT，自动切分、入库、向量化 |
| Agent 调客户后端 | Tools + Function Calling | 在控制台注册 HTTP API、参数 Schema、认证、超时、重试；Agent 会在对话中自动调用 |
| 工作流回传业务系统 | Workflow complete webhook | 工作流完成后可把收集到的表单数据 POST 到客户系统 |
| 排障与回放 | Audit APIs | 可按 trace/session 查看工具调用、RAG、工作流执行记录 |

---

## 环境变量

### 生产环境必填

```bash
HLAB_CORS_ORIGINS=https://你的域名.com    # 限制跨域
HLAB_DISABLE_AUTH=false                    # 开启认证
HLAB_SECRET_KEY=随机64位字符串              # JWT 签名密钥
```

### 完整配置表

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HLAB_DATABASE_URL` | `sqlite+aiosqlite:///./data/hlab.db` | 数据库（开发 SQLite，生产建议 PostgreSQL） |
| `HLAB_LLM_PROVIDER` | `openai_compatible` | LLM 供应商类型 |
| `HLAB_LLM_BASE_URL` | `http://localhost:11434/v1` | LLM API 地址 |
| `HLAB_LLM_API_KEY` | （空） | API 密钥 |
| `HLAB_LLM_MODEL` | `qwen-flash` | 默认模型 |
| `HLAB_LLM_TIMEOUT` | `60` | LLM 超时（秒） |
| `HLAB_EMBEDDING_PROVIDER` | `local` | 嵌入模型来源（`local` = 本地 sentence-transformers） |
| `HLAB_EMBEDDING_MODEL` | `BAAI/bge-m3` | 嵌入模型（首次运行自动下载） |
| `HLAB_HF_ENDPOINT` | `https://hf-mirror.com` | HuggingFace 镜像，可按客户网络切换为内网镜像或官方源 |
| `HLAB_ASR_PROVIDER` | `dashscope_qwen` | 语音转文字提供商：`dashscope_qwen`、`funasr_http`、`openai_compatible`、`disabled` |
| `HLAB_ASR_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | ASR 云 API 或自部署 FunASR HTTP 地址 |
| `HLAB_ASR_API_KEY` | （空） | ASR API Key；控制台保存的 key 会覆盖该环境变量 |
| `HLAB_ASR_MODEL` | `qwen3-asr-flash` | ASR 模型名；自部署服务可按自己的模型名配置 |
| `HLAB_ASR_TIMEOUT` | `60` | ASR 转写超时（秒） |
| `HLAB_ASR_MAX_FILE_MB` | `10` | 单个音频文件大小限制；自部署 FunASR 可按需要调高 |
| `HLAB_ASR_CONFIG_PATH` | `./data/asr_config.json` | 控制台保存 ASR 配置的位置；Docker 部署会持久化到 data volume |
| `HLAB_ASR_FUNASR_PATH` | `/transcribe` | 自部署 FunASR HTTP 服务的转写路径 |
| `HLAB_VECTOR_STORE` | `faiss` | 向量存储（`faiss` 或 `pgvector`） |
| `HLAB_DISABLE_AUTH` | `true` | 关闭认证（生产必须设为 false） |
| `HLAB_AUDIT_ENABLED` | `true` | 启用审计日志 |

---

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | FastAPI, SQLAlchemy (async), Pydantic v2, FAISS (HNSW), jieba, sentence-transformers |
| 前端 | React 18, TypeScript, Ant Design v5, Vite, react-i18next |
| 数据库 | SQLite（开发）/ PostgreSQL（生产） |
| LLM | OpenAI 兼容 API（支持 Function Calling） |
| 嵌入 | bge-m3（1024维），级联降级到 MiniLM 等小模型 |
| 向量索引 | FAISS IndexHNSWFlat（M=32, efConstruction=200） |

---

## 开发

```bash
# 后端（热重载）
source venv/bin/activate
pip install -e ".[rag,dev]"
python -m uvicorn server.main:app --reload --port 8000

# 前端（热重载，API 自动代理到 :8000）
cd console && npm install && npm run dev

# 构建前端
cd console && npm run build && cp -r dist/ ../static/

# 运行测试
python tests/e2e_full_test.py          # API E2E 测试
npx playwright test tests/e2e/         # 浏览器 E2E 测试
```

---

## License

MIT
