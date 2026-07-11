**English** | [中文](README.md)

# Aezab

A self-hosted agent builder with a web console and API-first runtime for agents, knowledge bases, workflows, external tools, voice input, and audit traces.

> **Naming note**: Aezab is the current public brand name. `HlAB` (Headless AI Agent Builder) is
> the historical codename — the environment variable prefix `AEZAB_` (with `HLAB_` kept as a
> backward-compatible alias), the SDK package name `hlab-client`, and some code comments/file
> names still use HlAB. They refer to the same project, not two different products.

Aezab provides two integration surfaces: a console for configuration and testing, and backend APIs for customer apps, CRMs, support desks, internal dashboards, or automation pipelines. It currently covers agent management, RAG retrieval, the workflow engine, tool calling, ASR, integrations, Playground, and audit traces, and is under active development.

## ⚡ Live in 5 Minutes

Here's the full path from zero to a working "knowledge-base support bot".

Requirements: Docker 24+ and Docker Compose v2 (the only hard requirement); Python 3.10+ only if you run the backend from source; Node.js 18+ only for frontend development or manual builds.

1. **Clone and start**:

   ```bash
   git clone https://github.com/AbysenAI/aezab.git
   cd aezab
   cp .env.example .env
   docker compose up -d --build
   ```

   This also starts `redis` and `ollama`: on first boot, `ollama-init` automatically downloads a small ~1GB local model (`qwen2.5:1.5b`) so the console has a working model out of the box. If you plan to use a cloud LLM instead (recommended), skip the wait and open the console directly — the first-run wizard walks you through it. Check readiness with `curl http://localhost:8000/health`.

2. **Open the console and follow the wizard**: visit `http://localhost:8000`. On first open you'll be guided through creating an admin account and logging in. If there's no model config or agent yet, a three-step setup wizard appears automatically — pick an LLM provider card (Qwen / Zhipu / MiniMax / OpenAI / local Ollama / custom, just paste an API key) → test the connection → create an agent from a template in one click ("Knowledge Support", "Repair Ticket", or "Booking") → jump into the Playground to test it. Pick "Knowledge Support" here.

3. **Upload knowledge**: open the **Knowledge** page and upload your own FAQ / product docs for the agent you just created (TXT / MD / PDF / DOCX / CSV / XLSX supported — see "Knowledge Upload Standard" below).

4. **Embed it on your site**: open **Integrations**, create an API key scoped to `invoke` only, then copy a `<script>` snippet into your website — it renders a floating chat bubble. A full runnable example is in `examples/widget-demo.html`; the attribute reference is in [`docs/integration.md`](docs/integration.md), section 9.

> This is only one example of what the framework can do. Swap the template and the same flow becomes a repair-ticket bot or a booking assistant. Go further and you can compose workflows, tool calls, and multi-agent delegation into almost any business process — order lookup, internal knowledge assistants, approval flows, and more. Aezab is general-purpose agent infrastructure, not a customer-support product.
>
> Hit a snag (model won't connect, upload stuck, port conflict, ...)? See [`docs/troubleshooting_en.md`](docs/troubleshooting_en.md).

## Use Cases

- Customer support: answer from product docs, service policies, and support manuals.
- Ticketing: repairs, applications, approvals, form collection, order lookup, and CRM updates.
- Internal operations: policy Q&A, process execution, system lookup, and cross-team routing.
- Industry deployments: run in a customer environment with their own models, data, and business APIs.

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

## Core Concepts

| Concept | Description |
| --- | --- |
| Agent | Runtime unit for a business scenario. It includes prompts, model config, capability bindings, and runtime policy. |
| Capability | A set of resources an agent can use: knowledge, workflows, tools, and agent delegation. |
| Knowledge | Document and structured knowledge sources with upload, chunking, indexing, retrieval, and citations. |
| Workflow | Multi-step business process with field collection, file upload, validation, tool calls, and callbacks. |
| Tool | External HTTP API or built-in function exposed to the agent through function calling. |

## Configuration Guide

### Model Configuration

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

Other OpenAI-compatible providers (MiniMax, DeepSeek, vLLM, etc.) follow the same shape — just swap `base_url`/`model`. Runtime priority: `agent-bound model config > tenant default model config > .env default`.

### Configure an Agent

Recommended setup path:

1. Create an agent in **Agents** and set its name, description, system prompt, and model config.
2. Create a knowledge source in **Knowledge** and upload documents.
3. Bind available knowledge sources in **Agents -> Capabilities**.
4. Configure business processes in **Workflows** and bind them to the agent.
5. Register external APIs in **Tools** or **Integrations -> Outbound Tools** and bind them to the agent.
6. Test conversation, retrieval, workflows, tool calls, and traces in **Playground**.

**Agents -> Capabilities is the source of truth for runtime capability configuration.** Bindings created from Integrations are shortcuts and write back to the same Capabilities model.

### Capability Triggering

Aezab uses a conversation-first runtime: bound capabilities are converted into function definitions, and the model decides which to call from conversation context.

| Capability | Main trigger material |
| --- | --- |
| Knowledge | Source, domain, document content, retrieval results. |
| Workflow | Workflow name, description, step definitions, optional agent-specific instruction. |
| Tool | Tool name, description, parameter schema, response schema. |
| Agent delegation | Agent connection settings and target agent description. |

If triggering is unstable, adjust the capability's own name/description/schema first, rather than relying on agent-specific instructions.

### Knowledge Upload Standard

Uploaded files are parsed into text, recursively split by `chunk_size`/`chunk_overlap`, then indexed for BM25, vector search, and fast lookup. The default single-file limit is `AEZAB_KNOWLEDGE_MAX_UPLOAD_MB=50`.

- TXT/MD: UTF-8/GB18030/UTF-16, one knowledge point per paragraph, `Question: ... Answer: ...` works well for FAQs.
- DOCX/PDF: use real text, not scanned images; OCR scanned files first.
- CSV/XLSX: keep a header row; tables are converted into `Field: Value` retrieval text, which works better than raw cell concatenation.
- Avoid uploading: encrypted PDFs, pivot-style spreadsheets, embedded attachments, or text that only exists inside images.

Before publishing an agent, verify recall in **Knowledge -> Retrieval Test** and final behavior in **Playground**.

## Integration Model

Integrations is a developer workspace, not a second agent configuration system.

| Category | Purpose |
| --- | --- |
| Inbound API | External systems call Aezab agents (REST + SSE, plus Python/JS SDKs). |
| ASR | Upload audio and receive transcription text for voice input. |
| Outbound Tools | Agents call customer backend APIs, such as ticket creation, order lookup, or CRM updates. |
| Workflow Webhooks | Aezab calls customer systems when a workflow completes or reaches a key step, with HMAC signing. |
| Embeddable Widget | A `<script>` tag embeds a chat bubble on any page (see step 4 of "Live in 5 Minutes"). |
| Trace & Debug | Inspect recent calls, event types, and audit logs. |

Typical flow:

```text
Customer App
  -> POST /api/v1/invoke
  -> Agent Runtime
  -> RAG / Workflow / Tool Calling
  -> Customer API or final response
```

All APIs require an `X-API-Key` header. Create a key from the console's **Integrations** page (scope it to `invoke`). The full interactive API reference is at `http://localhost:8000/docs` (Swagger UI). Python/JS SDKs (source install, not yet published to a package registry), webhook signature verification, and rate-limit/retry semantics are in [`docs/integration.md`](docs/integration.md).

Example call:

```bash
curl -X POST http://localhost:8000/api/v1/invoke \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your-key>" \
  -d '{"agent_id": "agent-id", "message": "I need to report a leaking kitchen pipe.", "tenant_id": "default"}'
```

For streaming, swap the path for `/api/v1/invoke/stream` (same payload shape, add `curl -N` to keep the stream open). Document upload (`POST /knowledge/upload`) and audio transcription (`POST /asr/transcribe`) examples are in [`docs/integration.md`](docs/integration.md).

## Run From Source

Backend:

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e ".[rag]"

cp .env.example .env
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

The first time you open the console, it will guide you through creating an admin account. After logging in, the first-run setup wizard appears automatically if there's no model or agent yet. The database and vector index are also backed up automatically every 24 hours to `./data/backups/` — running from source and running via Docker share the same logic, no extra setup required.

Build the frontend:

```bash
cd console
npm install
npm run build
cp -r dist/. ../static/
```

> `static/widget.js` is hand-maintained, not part of the `console/dist` build output. The
> command above merge-copies (`dist/.`) without emptying `static/` first. **Do not** sync with
> `rm -rf static/*` or `rsync --delete` — that wipes `widget.js` too and breaks the widget with
> a 404 in production (this bit us during this session).

Frontend development:

```bash
cd console
npm install
npm run dev
```

## Project Structure

```text
aezab/
  server/            # FastAPI backend: api/ engine/ models/ schemas/ config.py
  console/src/       # React console: pages/ api.ts i18n/
  static/            # Built console assets + widget.js
  Dockerfile
  docker-compose.yml
  pyproject.toml
```

## Deployment & Operations

- SQLite is suitable for development and small trials; PostgreSQL, Redis, HTTPS, reverse proxying, and a backup strategy are recommended for production.
- API keys and model credentials should be stored in environment variables or a deployment secret manager.
- The database and vector index are backed up automatically every 24 hours to `./data/backups/` (`AEZAB_BACKUP_INTERVAL_HOURS` is configurable); the console's Settings page can trigger an on-demand backup or download one. Database schema migrations run automatically at startup via Alembic (`ensure_schema()`) — no manual `ALTER TABLE` needed. Log retention and rate limits (e.g. `AEZAB_RATE_LIMIT_PER_MINUTE`) are also configurable via environment variables.
- The full production deployment checklist (single-process architecture constraints, SSE reverse-proxy config, widget security) is in [`docs/deployment.md`](docs/deployment.md).

## Documentation Index

- [`docs/troubleshooting_en.md`](docs/troubleshooting_en.md) — common issues.
- [`docs/deployment.md`](docs/deployment.md) — production deployment checklist.
- [`docs/integration.md`](docs/integration.md) — SDK / API / webhook / widget integration details.
- [`docs/migrations.md`](docs/migrations.md) — database migrations (Alembic).
- [`docs/development.md`](docs/development.md) — dev conventions and local checks (for contributors; Chinese only).

## License

MIT
