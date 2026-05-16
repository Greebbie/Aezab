**English** | [中文](README.md)

# HlAB — Headless AI Agent Builder

A privately deployable AI Agent platform with a web management console. Visually create, configure, and run conversational AI agents — with knowledge base Q&A, multi-step workflows, external API tool calling, and multi-agent collaboration — while exposing a Headless API and SSE streaming interface for integration into any system.

**Zero vendor lock-in.** Compatible with any OpenAI-format LLM — local Ollama, Alibaba DashScope (Qwen), vLLM, OpenAI, ZhipuAI, MiniMax, DeepSeek, and more.

---

## Feature Overview

| Feature | Description |
|---------|-------------|
| **Multi-Agent Management** | Create multiple independent agents, each with its own system prompt, knowledge base, tools, and workflows |
| **Knowledge Base (RAG)** | Upload TXT / PDF / DOCX / Excel / CSV documents; auto-chunking, vectorization, and 3-channel hybrid retrieval + RRF fusion |
| **Workflow Engine** | Define multi-step business processes (e.g. repair requests, application approvals); LLM auto-triggers based on user intent |
| **Tool Calling** | Register external HTTP APIs as tools; agents invoke them automatically via Function Calling |
| **Multi-Agent Collaboration** | Agents can delegate tasks to each other with built-in depth limits (max 3) and cycle detection |
| **LLM Config Management** | Manage multiple LLM provider configs, assign per-agent, with one-click template loading and connectivity testing |
| **Voice Input / ASR** | Upload audio or record in the browser; connect cloud ASR or self-hosted FunASR with console-managed config and direct testing |
| **Web Console** | 10-page React management interface covering all features with visual operations |
| **Headless API** | All features accessible via REST API + SSE streaming, 70+ endpoints |
| **i18n** | Full Chinese + English interface with browser auto-detection |

---

## Quick Start

### Requirements

- **Docker 24+ and Docker Compose v2** (recommended deployment path)
- **Python 3.10+**
- **Node.js 18+** (only needed if modifying frontend or building from source)

### Step 1: Clone

```bash
git clone https://github.com/your-repo/headlessAIAgentPlatform.git
cd headlessAIAgentPlatform
```

### Step 2: Recommended Docker Compose deployment

Docker Compose builds the backend and console, then starts Redis, optional local Ollama, and persistent data volumes.

```bash
cp .env.example .env
```

Edit `.env`. For the bundled Ollama service, keep:

```bash
HLAB_LLM_PROVIDER=openai_compatible
HLAB_LLM_BASE_URL=http://ollama:11434/v1
HLAB_LLM_MODEL=qwen2.5
```

For a cloud LLM, set `HLAB_LLM_BASE_URL`, `HLAB_LLM_API_KEY`, and `HLAB_LLM_MODEL` to your provider values. Voice input defaults to DashScope Qwen ASR and can be configured with:

```bash
HLAB_ASR_PROVIDER=dashscope_qwen
HLAB_ASR_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
HLAB_ASR_API_KEY=sk-your-dashscope-key
HLAB_ASR_MODEL=qwen3-asr-flash
```

Start:

```bash
docker compose up -d --build server
docker compose logs -f server
```

Check health:

```bash
curl http://localhost:8000/health
```

Open the console at `http://localhost:8000`.

Persistent storage:

- App data, SQLite, uploads, and console-managed ASR config: Docker volume `hlab-data`
- Local embedding model cache: Docker volume `hlab-model-cache`
- Local Ollama models: Docker volume `ollama-models`

### Step 3: Source-code deployment - install dependencies

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e ".[rag]"           # Install backend + RAG dependencies
```

Dependencies are managed via `pyproject.toml`. The `[rag]` optional group includes FAISS, sentence-transformers, jieba, pypdf, python-docx, etc.

> **Apple Silicon users**: If `faiss-cpu` fails to install, try `pip install faiss-cpu --no-cache-dir`.

### Step 4: Source-code deployment - configure environment variables

```bash
cp .env.example .env
```

Edit `.env` — only LLM connection info is required to start:

```bash
# ─── Option A: Local Ollama (free, no API key needed) ───
HLAB_LLM_PROVIDER=openai_compatible
HLAB_LLM_BASE_URL=http://localhost:11434/v1
HLAB_LLM_MODEL=qwen2.5

# ─── Option B: Alibaba DashScope ───
HLAB_LLM_PROVIDER=dashscope
HLAB_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
HLAB_LLM_API_KEY=sk-your-key
HLAB_LLM_MODEL=qwen-flash

# ─── Option C: OpenAI / MiniMax / DeepSeek / any OpenAI-compatible ───
HLAB_LLM_PROVIDER=openai_compatible
HLAB_LLM_BASE_URL=https://api.openai.com/v1
HLAB_LLM_API_KEY=sk-your-key
HLAB_LLM_MODEL=gpt-4o
```

> **LLM config priority**: `.env` values are the startup fallback. After the server is running, you can create multiple LLM configs in the **LLM Configs** console page (Ollama / DashScope / OpenAI / vLLM etc.), then assign each agent its own config in the **Agents** page.
>
> Priority: Agent-bound config > Tenant default config > `.env` fallback.

### Step 5: Source-code deployment - start the server

```bash
source venv/bin/activate
HLAB_DISABLE_AUTH=true python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

The server auto-creates the SQLite database and all tables on first start. No manual migration needed.

### Step 6: Open the console

**Production mode** (requires frontend build):

```bash
cd console
npm install
npm run build                     # Output to console/dist/
cp -r dist/ ../static/            # Copy to static/ for backend hosting
cd ..
# Restart backend, visit http://localhost:8000
```

**Development mode** (hot reload, separate frontend):

```bash
cd console
npm install
npm run dev
# Open http://localhost:3000, API auto-proxied to :8000
```

---

## Usage Guide

After starting the server, the typical workflow is:

### 1. Configure LLM

Go to **LLM Configs** page -> Click "Create Config" -> Select provider (e.g. DashScope) -> Click "Load Template" to auto-fill parameters -> Enter API Key -> Click "Test Config" to verify connectivity -> Save.

You can create multiple configs (e.g. a cheap qwen-flash for daily use, a powerful qwen-max for complex scenarios) and set one as default.

### 2. Configure Voice Input (optional)

Go to **Settings -> Voice Input / ASR Configuration**:

- Select an ASR Provider: DashScope Qwen ASR, OpenAI Compatible, self-hosted FunASR HTTP, or Disabled
- Click **Apply Provider Defaults** to auto-fill common base URL, model, timeout, and file size values
- Enter the ASR API Key and click **Save ASR Configuration**
- Click **Test ASR Upload** to upload an audio file and verify transcription directly from Settings

Saved overrides are stored in `data/asr_config.json`; Docker deployments persist this file in the `hlab-data` volume. The page shows Key Source:

- `environment`: using `HLAB_ASR_API_KEY` from `.env`
- `saved_config`: using the API key saved from the console
- `llm_config`: reusing the LLM key when ASR and LLM share the same base URL
- `missing`: no available key

The voice button in Playground supports audio upload and browser recording. Successful transcription is automatically inserted into the message input.

### 3. Create Agent

Go to **Agents** page -> Click "Create Agent" ->

- **Basic Info** tab: Fill in name, description, system prompt; select LLM config
- **Capabilities** tab: Configure agent capabilities (knowledge base, workflows, tools, delegation)
- **Advanced** tab: Response format, risk control settings

### 4. Add Knowledge Base (optional)

Go to **Knowledge** page -> Create a knowledge source -> Upload documents (TXT / PDF / DOCX / Excel / CSV) -> System auto-chunks and vectorizes.

You can also manually add KV entity entries (e.g. "Office phone: 021-12345678") for exact-match instant lookups.

Bind the knowledge domain in the Agent's Capabilities tab, and the agent can automatically answer questions from the knowledge base.

> **Domain isolation**: Different domains are completely isolated. An agent bound to the "hr" domain will never return results from the "sales" domain.

### 5. Create Workflow (optional)

Go to **Workflows** page -> Create workflow -> Define steps (collect info, confirm, complete, etc.) -> Each step can have field types, validation rules, file upload, and LLM-assisted validation.

Bind the workflow in the Agent's Capabilities tab. The agent will auto-trigger it based on user intent (e.g. when user says "I need to submit a repair request").

### 6. Register Tools (optional)

Go to **Tools** page -> Register an external HTTP API (fill in URL, Method, parameter Schema) -> Test connectivity.

Bind tools in the Agent's Capabilities tab. The agent will call them via Function Calling during conversations.

### 7. Test Conversation

Go to **Playground** page -> Select an agent -> Start chatting. The right panel shows real-time Function Calling traces, retrieval results, latency breakdown, and citations.

---

## Architecture

### Conversation Pipeline

```
User Message
    |
    v
+------------------------------------------+
|           Agent Runtime                   |
|                                           |
|  1. Risk check (keyword filtering)        |
|  2. Intent detection (LLM + keyword)      |
|     |-- Action intent -> skip pre-search  |
|     +-- Info query -> parallel pre-search |
|  3. Query rewriting (multi-turn context)  |
|  4. Build skill tool list (OpenAI format) |
|  5. LLM + Function Calling               |
|     |-- search_knowledge(query)           |
|     |-- start_workflow_xxx(reason)        |
|     |-- http_tool_xxx(params)             |
|     +-- delegate_to_xxx(message)          |
|  6. Execute tools -> return to LLM       |
|  7. Generate final response + followups  |
+------------------------------------------+
```

**Conversation-first architecture**: No explicit intent classifier or router layer. The LLM autonomously decides when to call tools based on conversation context. All skills (knowledge search, workflow start, HTTP tools, agent delegation) are exposed as OpenAI Function Calling definitions.

### Knowledge Retrieval Pipeline (RAG)

```
User Query
    |
    +--- Exact Match (KV)      <50ms    Entity key lookup
    +--- Vector Search (HNSW)  <200ms   bge-m3 embedding -> FAISS ANN
    +--- Keyword Search (BM25) <100ms   jieba tokenization -> BM25 scoring
         |
         v
    RRF Fusion (k=60, weighted merge of all channels)
         |
         v
    [Optional] Cross-encoder Reranking (bge-reranker-v2-m3)
         |
         v
    Top-K results fed to LLM for answer generation
```

All three channels run **in parallel** (asyncio.gather). If any channel fails, the others continue unaffected.

---

## Project Structure

```
headlessAIAgentPlatform/
+-- server/                        # FastAPI backend
|   +-- api/                       # REST API (70+ endpoints)
|   |   +-- invoke.py              #   Conversation invoke + SSE streaming
|   |   +-- agents.py              #   Agent CRUD
|   |   +-- agent_capabilities.py  #   Agent capabilities (auto-manages skills)
|   |   +-- knowledge.py           #   Knowledge base + document upload
|   |   +-- workflows.py           #   Workflow management
|   |   +-- tools.py               #   Tool management
|   |   +-- llm_configs.py         #   LLM config + provider templates + test
|   |   +-- asr.py                 #   Voice transcription config + upload API
|   |   +-- performance.py         #   Performance presets + runtime config
|   |   +-- audit.py               #   Audit logs
|   |   +-- vector_admin.py        #   Vector index management
|   +-- engine/                    # Core engine
|   |   +-- agent_runtime.py       #   Agent pipeline: intent + pre-retrieval + function calling
|   |   +-- knowledge_retriever.py #   3-channel hybrid retrieval + RRF + cross-encoder reranker
|   |   +-- llm_adapter.py         #   Multi-provider LLM adapter + function calling
|   |   +-- asr.py                 #   Cloud ASR + self-hosted FunASR provider layer
|   |   +-- workflow_executor.py   #   Workflow executor (validation + file upload + LLM validation)
|   |   +-- tool_executor.py       #   Tool executor + exponential backoff retry
|   |   +-- vector_store.py        #   FAISS HNSW vector index + embedding model cascade fallback
|   +-- models/                    # SQLAlchemy ORM models (13 tables)
|   +-- schemas/                   # Pydantic request/response schemas
|   +-- config.py                  # Environment variable config
+-- console/                       # React + Ant Design frontend
|   +-- src/
|       +-- api.ts                 #   Centralized API client
|       +-- pages/                 #   10 management pages
|       +-- i18n/                  #   Chinese + English translations
+-- tests/                         # Test suite (unit + E2E)
+-- pyproject.toml                 # Python dependencies
+-- .env.example                   # Environment variable template
```

---

## Console Pages

| Page | Purpose |
|------|---------|
| **Dashboard** | Overview: agent count, request volume, average latency, circuit breaker status |
| **Playground** | Real-time chat with agents; right panel shows function calling chain, citations, latency breakdown |
| **Agents** | Create/edit agents with tabbed config: Basic Info, Capabilities (knowledge/workflow/tools/delegation), Advanced |
| **Skills** | Manage standalone skills (auto-managed skills from Agent Capabilities are hidden) |
| **Workflows** | Define multi-step business processes with field types, validation rules, file upload |
| **Knowledge** | Upload documents (TXT/PDF/DOCX/Excel/CSV), manage sources, add KV entities, view chunks, test retrieval |
| **Tools** | Register external HTTP APIs as tools, configure parameter schemas, test connectivity |
| **LLM Configs** | Manage LLM provider configs with one-click templates (Ollama/DashScope/OpenAI/vLLM/ZhipuAI) and connection testing |
| **Settings** | Performance presets, embedding warmup/download, ASR voice input configuration, upload testing, and advanced tuning |
| **Audit** | Full call chain replay with event types, timing, and input/output for each step |

---

## Supported LLM Providers

| Provider | Config Type | Recommended Models | Notes |
|----------|-------------|-------------------|-------|
| **Ollama** (local) | `openai_compatible` | qwen2.5, llama3, mistral | Free, no API key, requires local Ollama |
| **DashScope** (Alibaba) | `dashscope` | qwen-flash, qwen-plus, qwen-max | Cost-effective, qwen-flash is very cheap |
| **OpenAI** | `openai_compatible` | gpt-4o, gpt-4o-mini | Direct compatibility |
| **vLLM** | `openai_compatible` | Any vLLM-deployed model | Self-hosted inference |
| **ZhipuAI** | `zhipu` | glm-4, glm-4-flash | Chinese LLM |
| **MiniMax** | `openai_compatible` | MiniMax-M2.7 | via api.minimax.chat |
| **DeepSeek** | `openai_compatible` | deepseek-chat, deepseek-coder | via api.deepseek.com |

Any provider using the OpenAI-compatible API format can be connected via `openai_compatible`.

---

## Voice Input / ASR

| Option | Provider | Use Case |
|--------|----------|----------|
| DashScope Qwen ASR | `dashscope_qwen` | Default China-friendly cloud API for quick setup |
| OpenAI-compatible ASR | `openai_compatible` | Cloud services that support audio input through `/chat/completions` |
| Self-hosted FunASR HTTP | `funasr_http` | Existing local or intranet ASR service |
| Disabled | `disabled` | Turn off voice input |

Console path: **Settings -> Voice Input / ASR Configuration**. Saved changes take effect immediately without restart. Playground voice input supports:

- Audio file upload: WAV, MP3, M4A, AAC, OGG, OPUS, FLAC, WEBM
- Browser recording: start recording -> stop and transcribe -> auto-fill the chat input

---

## Embedding Model

The default embedding model is **BAAI/bge-m3** (1024-dimensional, multilingual).

- **Download source**: Defaults to `hf-mirror.com` and is configurable via `HLAB_HF_ENDPOINT`. Actual availability still depends on the customer's network and firewall policy.
- **Cascade fallback**: If bge-m3 fails to download, automatically falls back to: bge-small-zh (512D) -> MiniLM-L6 (384D) -> multilingual-MiniLM (384D).
- **First use / warmup**: Local models are downloaded to `~/.cache/huggingface`. Docker Compose persists this cache in `hlab-model-cache`. Admins can trigger download from **Settings -> Embedding Model -> Warm Up / Download**.
- **API alternative**: Set `HLAB_EMBEDDING_PROVIDER=dashscope` to use DashScope's embedding API instead of local models.

---

## Performance Presets

Switch via the **Settings** page or API. Affects retrieval strategy and parameters:

| Preset | Retrieval Timeout | Vector efSearch | Reranker | Use Case |
|--------|-------------------|-----------------|----------|----------|
| **Fast** | 3s | 64 | Off | Real-time chat, low latency |
| **Balanced** (default) | 5s | 128 | Off | General purpose |
| **Accurate** | 10s | 256 | On (bge-reranker-v2-m3) | High-accuracy professional Q&A |

Individual parameters can also be fine-tuned in the Settings page (retrieval channel weights, Top-K, timeouts, etc.). Changes take effect immediately without restart.

---

## Core API

All APIs are prefixed with `/api/v1`. Visit `http://localhost:8000/docs` for the full interactive Swagger documentation.

### Chat with an Agent

```bash
# Synchronous
curl -X POST http://localhost:8000/api/v1/invoke \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "your-agent-id", "message": "Hello"}'

# SSE Streaming
curl -X POST http://localhost:8000/api/v1/invoke/stream \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "your-agent-id", "message": "Hello"}'
# Returns: event: status -> event: answer -> event: done
```

### Upload Document

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -F "file=@handbook.pdf" \
  -F "source_id=source-id" \
  -F "domain=docs" \
  -F "chunk_size=500" \
  -F "chunk_overlap=50"
```

Supports `.txt`, `.pdf`, `.docx`, `.xlsx`, `.csv`. Auto recursive chunking (paragraph -> line -> sentence -> character) and vectorization.

### ASR Transcription

```bash
# Check ASR config and key source
curl http://localhost:8000/api/v1/asr/config

# Upload audio and transcribe
curl -X POST http://localhost:8000/api/v1/asr/transcribe \
  -F "file=@voice.wav"
```

### Health Check

```bash
curl http://localhost:8000/health
# {"status": "ok", "version": "0.1.0", "components": {...}}
```

---

## Environment Variables

### Production Required

```bash
HLAB_CORS_ORIGINS=https://yourdomain.com    # Restrict CORS origins
HLAB_DISABLE_AUTH=false                      # Enable API key auth
HLAB_SECRET_KEY=random-64-char-string        # JWT signing key
```

### Full Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HLAB_DATABASE_URL` | `sqlite+aiosqlite:///./data/hlab.db` | Database (SQLite for dev, PostgreSQL for production) |
| `HLAB_LLM_PROVIDER` | `openai_compatible` | LLM provider type |
| `HLAB_LLM_BASE_URL` | `http://localhost:11434/v1` | LLM API base URL |
| `HLAB_LLM_API_KEY` | (empty) | Cloud provider API key |
| `HLAB_LLM_MODEL` | `qwen-flash` | Default model name |
| `HLAB_LLM_TIMEOUT` | `60` | LLM call timeout (seconds) |
| `HLAB_EMBEDDING_PROVIDER` | `local` | Embedding provider (`local` = sentence-transformers) |
| `HLAB_EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding model (auto-downloads on first run) |
| `HLAB_HF_ENDPOINT` | `https://hf-mirror.com` | HuggingFace mirror URL; switch to an internal mirror or official endpoint as needed |
| `HLAB_ASR_PROVIDER` | `dashscope_qwen` | Speech-to-text provider: `dashscope_qwen`, `funasr_http`, `openai_compatible`, or `disabled` |
| `HLAB_ASR_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Cloud ASR API or self-hosted FunASR HTTP endpoint |
| `HLAB_ASR_API_KEY` | (empty) | ASR API key; console-saved key overrides this environment value |
| `HLAB_ASR_MODEL` | `qwen3-asr-flash` | ASR model name; self-hosted services can use their own model value |
| `HLAB_ASR_TIMEOUT` | `60` | ASR transcription timeout in seconds |
| `HLAB_ASR_MAX_FILE_MB` | `10` | Max audio file size; self-hosted FunASR deployments can raise this |
| `HLAB_ASR_CONFIG_PATH` | `./data/asr_config.json` | Path for console-managed ASR config; Docker deployments persist it in the data volume |
| `HLAB_ASR_FUNASR_PATH` | `/transcribe` | Transcription path for self-hosted FunASR HTTP services |
| `HLAB_VECTOR_STORE` | `faiss` | Vector storage backend (`faiss` or `pgvector`) |
| `HLAB_DISABLE_AUTH` | `true` | Disable API auth (MUST be false in production) |
| `HLAB_AUDIT_ENABLED` | `true` | Enable audit logging |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | FastAPI, SQLAlchemy (async), Pydantic v2, FAISS (HNSW), jieba, sentence-transformers |
| **Frontend** | React 18, TypeScript, Ant Design v5, Vite, react-i18next |
| **Database** | SQLite (dev) / PostgreSQL (production) |
| **LLM** | OpenAI-compatible API format (supports Function Calling) |
| **Embedding** | bge-m3 (1024D) with cascade fallback to smaller models |
| **Vector Index** | FAISS IndexHNSWFlat (M=32, efConstruction=200, configurable efSearch) |

---

## Development

### Backend

```bash
source venv/bin/activate
pip install -e ".[rag,dev]"
python -m uvicorn server.main:app --reload --port 8000
```

### Frontend

```bash
cd console
npm install
npm run dev                       # http://localhost:3000, auto-proxies API
```

### Build Frontend for Production

```bash
cd console
npm run build                     # Output to console/dist/
cp -r dist/ ../static/            # Copy to project root static/
```

The backend auto-detects the `static/` directory and serves the frontend (SPA mode) — no Nginx configuration needed.

### Run Tests

```bash
# API E2E test
python tests/e2e_full_test.py

# Playwright browser E2E test
npx playwright test tests/e2e/
```

---

## License

MIT
