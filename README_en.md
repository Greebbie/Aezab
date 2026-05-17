**English** | [中文](README.md)

# Aezab

A self-hosted agent builder with a web console and API-first runtime for agents, knowledge bases, workflows, external tools, voice input, and audit traces.

Aezab provides two integration surfaces: a console for configuration and testing, and backend APIs for customer apps, CRMs, support desks, internal dashboards, or automation pipelines.

## Use Cases

- Customer support: answer from product docs, service policies, and support manuals.
- Ticketing: repairs, applications, approvals, form collection, order lookup, and CRM updates.
- Internal operations: policy Q&A, process execution, system lookup, and cross-team routing.
- Industry deployments: run in a customer environment with their own models, data, and business APIs.

## Core Concepts

| Concept | Description |
| --- | --- |
| Agent | Runtime unit for a business scenario. It includes prompts, model config, capability bindings, and runtime policy. |
| Capability | A set of resources an agent can use: knowledge, workflows, tools, and agent delegation. |
| Knowledge | Document and structured knowledge sources with upload, chunking, indexing, retrieval, and citations. |
| Workflow | Multi-step business process with field collection, file upload, validation, tool calls, and callbacks. |
| Tool | External HTTP API or built-in function exposed to the agent through function calling. |
| Integration | Developer workspace for APIs, tools, ASR, webhooks, and trace debugging. |

## Features

| Area | Capabilities |
| --- | --- |
| Agent Management | Multiple agents, model selection, capability binding, agent delegation. |
| Knowledge / RAG | TXT, PDF, DOCX, XLSX, CSV upload; BM25, vector search, RRF fusion, optional reranking. |
| Workflow Engine | Sequential steps, field collection, file upload, LLM validation, failure handling, completion callbacks. |
| Tool Calling | HTTP tool registration, parameter schema, auth config, timeout, retry, connectivity test. |
| Voice / ASR | Browser recording, audio upload, DashScope/OpenAI-compatible ASR, self-hosted FunASR HTTP. |
| Playground | Conversation testing, RAG hits, tool calls, workflow triggers, latency, and errors. |
| Audit Trace | Trace id for each run, with retrieval, model, tool, and workflow events. |
| Headless API | `/invoke`, `/invoke/stream`, `/asr/transcribe`, and related APIs for external integration. |

## Quick Start

### Requirements

- Docker 24+ and Docker Compose v2
- Python 3.10+, only for running the backend from source
- Node.js 18+, only for frontend development or manual builds

### Clone

```bash
git clone https://github.com/AbysenAI/aezab.git
cd aezab
```

### Start With Docker Compose

```bash
cp .env.example .env
docker compose up -d --build server
docker compose logs -f server
```

Check the service:

```bash
curl http://localhost:8000/health
```

Open the console:

```text
http://localhost:8000
```

Default services:

| Service / volume | Purpose |
| --- | --- |
| `server` | FastAPI backend and static frontend. |
| `redis` | Runtime dependency. |
| `ollama` | Optional local model service. |
| `aezab-data` | SQLite, uploads, vector indexes, ASR config. |
| `aezab-model-cache` | Local embedding model cache. |
| `ollama-models` | Ollama model data. |

## Model Configuration

Aezab requires at least one working LLM. Set a default model in `.env`, or manage multiple model configs from the console.

Local Ollama:

```bash
AEZAB_LLM_PROVIDER=openai_compatible
AEZAB_LLM_BASE_URL=http://ollama:11434/v1
AEZAB_LLM_MODEL=qwen2.5
```

DashScope:

```bash
AEZAB_LLM_PROVIDER=dashscope
AEZAB_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AEZAB_LLM_API_KEY=sk-your-key
AEZAB_LLM_MODEL=qwen-flash
```

OpenAI-compatible providers such as MiniMax, DeepSeek, or vLLM:

```bash
AEZAB_LLM_PROVIDER=openai_compatible
AEZAB_LLM_BASE_URL=https://api.example.com/v1
AEZAB_LLM_API_KEY=sk-your-key
AEZAB_LLM_MODEL=your-model
```

Runtime priority:

```text
Agent-bound model config > tenant default model config > .env default
```

## Configure an Agent

Recommended setup path:

1. Create an agent in **Agents** and set its name, description, system prompt, and model config.
2. Create a knowledge source in **Knowledge** and upload documents.
3. Bind available knowledge sources in **Agents -> Capabilities**.
4. Configure business processes in **Workflows** and bind them to the agent.
5. Register external APIs in **Tools** or **Integrations -> Outbound Tools** and bind them to the agent.
6. Test conversation, retrieval, workflows, tool calls, and traces in **Playground**.

**Agents -> Capabilities is the source of truth for runtime capability configuration.** Bindings created from Integrations are shortcuts and write back to the same Capabilities model.

## Capability Triggering

Aezab uses a conversation-first runtime. Bound capabilities are converted into function definitions, and the model selects which function to call from conversation context.

| Capability | Main trigger material |
| --- | --- |
| Knowledge | Source, domain, document content, retrieval results. |
| Workflow | Workflow name, description, step definitions, optional agent-specific instruction. |
| Tool | Tool name, description, parameter schema, response schema. |
| Agent delegation | Agent connection settings and target agent description. |

If triggering is unstable, adjust the capability name, description, and schema first. Agent-specific instructions are useful as additional constraints, not as the primary trigger system.

## Knowledge Upload Standard

Uploaded files are parsed into text, recursively split by `chunk_size` and `chunk_overlap`, then indexed for BM25, vector search, and fast lookup. DOCX, CSV, and XLSX all go through the same chunking pipeline after extraction. The default single-file limit is `AEZAB_KNOWLEDGE_MAX_UPLOAD_MB=50`.

| Format | Recommended standard |
| --- | --- |
| TXT / MD | Use UTF-8, GB18030, or UTF-16. Keep one knowledge point per paragraph. FAQ content can use `Question: ... Answer: ...`. |
| DOCX | Use real text, not scanned images. Headings, paragraphs, and tables are extracted. Move complex layouts, text boxes, and footnotes into normal body sections when possible. |
| CSV | Prefer a header row. UTF-8, UTF-8 BOM, and GB18030 are supported. Common delimiters include comma, semicolon, tab, and pipe. |
| XLSX | Each sheet is parsed separately. Prefer a header row. Formulas are read from their current calculated values. Flatten merged cells and pivot-style sheets before upload. |
| PDF | Text PDFs are supported. Scanned PDFs should be OCR-processed into text, DOCX, or TXT first. |

Tables are converted into retrieval-oriented field text, for example `Item: Repair phone | Value: 0571-88001234`. This works better than raw cell concatenation for support QA, policies, and form-like knowledge. Before publishing an agent, verify recall in **Knowledge -> Retrieval Test** and final behavior in **Playground**.

Avoid uploading scanned images, encrypted PDFs, pivot-style spreadsheets, embedded attachments, or text that only exists inside images. OCR or flatten those files into DOCX, TXT, or CSV first for more stable retrieval.

## Integration Model

Integrations is a developer workspace, not a second agent configuration system.

| Category | Purpose |
| --- | --- |
| Inbound API | External systems call Aezab agents. |
| ASR | Upload audio and receive transcription text for voice input. |
| Outbound Tools | Agents call customer backend APIs, such as ticket creation, order lookup, or CRM updates. |
| Workflow Webhooks | Aezab calls customer systems when a workflow completes or reaches a key step. |
| Trace & Debug | Inspect recent calls, event types, and audit logs. |

Typical flow:

```text
Customer App
  -> POST /api/v1/invoke
  -> Agent Runtime
  -> RAG / Workflow / Tool Calling
  -> Customer API or final response
```

## API Examples

Invoke an agent:

```bash
curl -X POST http://localhost:8000/api/v1/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent-id",
    "message": "I need to report a leaking kitchen pipe.",
    "tenant_id": "default"
  }'
```

Stream an agent response:

```bash
curl -N -X POST http://localhost:8000/api/v1/invoke/stream \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent-id",
    "message": "What is the property service phone number?",
    "tenant_id": "default"
  }'
```

Upload a document:

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -F "file=@handbook.docx" \
  -F "source_id=source-id" \
  -F "domain=default"
```

Transcribe audio:

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

The RAG pipeline includes fast lookup, FAISS HNSW vector search, jieba BM25, RRF fusion, and optional cross-encoder reranking. Knowledge sources support upload, update, deletion, chunk browsing, domain isolation, index rebuilds, and retrieval testing.

## Run From Source

Backend:

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e ".[rag]"

cp .env.example .env
AEZAB_DISABLE_AUTH=true python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Build the frontend:

```bash
cd console
npm install
npm run build
cp -r dist/ ../static/
```

Frontend development:

```bash
cd console
npm install
npm run dev
```

## Project Structure

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

## Development Checks

```bash
python -m pytest -q
npm --prefix console run build
docker compose config --quiet
```

## Deployment Notes

- SQLite is suitable for development and small trials.
- PostgreSQL, Redis, HTTPS, reverse proxying, and backups are recommended for production.
- API keys and model credentials should be stored in environment variables or a deployment secret manager.
- CORS, auth, model providers, vector indexes, and ASR providers can be configured through environment variables or the console.

## Project Status

Aezab currently covers agent configuration, RAG, workflows, tool calling, ASR, integrations, Playground testing, audit traces, and Docker deployment. The project is under active development and is intended as a self-hosted agent foundation for internal systems or customer projects.

## License

MIT
