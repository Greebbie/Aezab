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
| **性能调优** | 三档预设（快速/均衡/精确），支持实时调参，无需重启 |
| **审计追踪** | 每次对话的完整调用链：意图识别 → 知识检索 → 模型推理 → 工具调用，每步有耗时统计 |

---

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（仅修改前端时需要）

### 1. 克隆项目

```bash
git clone https://github.com/your-repo/headlessAIAgentPlatform.git
cd headlessAIAgentPlatform
```

### 2. 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e ".[rag]"           # 安装后端 + RAG 依赖（FAISS、jieba、sentence-transformers 等）
```

> **Apple Silicon**：如果 `faiss-cpu` 安装失败，试试 `pip install faiss-cpu --no-cache-dir`

### 3. 配置环境变量

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

### 4. 启动服务

```bash
HLAB_DISABLE_AUTH=true python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

首次启动会自动创建数据库和所有表，无需手动迁移。

> 嵌入模型（bge-m3, ~2.3GB）会在首次使用或设置页预热时下载。默认使用 `hf-mirror.com` 镜像源；实际可用性仍取决于客户网络和防火墙策略。

### 5. 打开控制台

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

### 第二步：创建智能体

进入「智能体」→ 点击 Create Agent → 填写名称和系统提示词 → 选择语言模型 → 完成创建。

### 第三步：添加知识库（可选）

进入「知识库」→ 创建知识源 → 上传文档（TXT / PDF / DOCX / Excel / CSV）。

系统自动完成：文档解析 → 递归分块 → 向量化（bge-m3 1024维）→ 建立 FAISS HNSW 索引。

回到「智能体」编辑页 → Capabilities 标签 → 绑定知识源。绑定后 Agent 就能回答知识库中的问题。

> **域隔离**：不同域（domain）的知识完全隔离，绑定 "hr" 域的 Agent 不会返回 "sales" 域的数据。

### 第四步：创建工作流（可选）

进入「工作流」→ 创建流程 → 定义步骤（信息收集 → 确认 → 完成）。

绑定到 Agent 后，当用户说"我要报修"时，Agent 会自动启动报修工作流引导用户逐步填写。

### 第五步：注册工具（可选）

进入「工具」→ 注册外部 HTTP API（填 URL、Method、参数 Schema）→ 测试连通。

绑定到 Agent 后，Agent 会在对话中通过 Function Calling 自动调用工具。

### 第六步：测试对话

进入「测试场」→ 选择 Agent → 开始对话。

右侧面板实时展示：引用来源（命中了哪些知识条目）、调用链追踪（每步的事件和耗时）。

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
│   ├── engine/                    # 核心引擎（Agent 执行、RAG 检索、LLM 适配、工作流）
│   ├── models/                    # 数据库模型（13 张表）
│   ├── schemas/                   # 请求/响应 Schema
│   └── config.py                  # 环境变量配置
├── console/                       # React + Ant Design 前端
│   └── src/
│       ├── api.ts                 # 集中式 API 客户端
│       ├── pages/                 # 10 个管理页面
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
| **智能体** | 创建/编辑 Agent，配置基本信息、能力绑定（知识库/工作流/工具）、高级设置 |
| **技能** | 管理独立技能（Agent 自动创建的托管技能已自动隐藏） |
| **工作流** | 定义多步骤业务流程，配置字段类型、校验规则、文件上传 |
| **知识库** | 上传文档、管理知识源、添加 KV 实体、查看分块详情、测试检索效果 |
| **工具** | 注册外部 HTTP API，配置参数 Schema，测试连通性 |
| **模型配置** | 管理 LLM 供应商，支持模板一键填充和连接测试 |
| **系统设置** | 性能预设切换（快速/均衡/精确）+ 高级参数微调 |
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

# 健康检查
curl http://localhost:8000/health
```

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
