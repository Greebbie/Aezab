<div align="center">

# Aezab

Self-hosted agent infrastructure — orchestration, hybrid retrieval, workflows, tools and full-trace audit behind one API.

**English** | [中文](README_zh.md)

![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square)
![React](https://img.shields.io/badge/react-18-61DAFB?style=flat-square)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?style=flat-square)

</div>

![Aezab overview](docs/images/aezab-overview.png)

## Why Aezab

Aezab is built for teams that need to embed agents into existing systems: the console handles configuration and testing, while the API integrates with your own apps, CRMs, support desks, internal dashboards, or automation pipelines. It's general-purpose agent infrastructure, not a customer-support product — the same orchestration primitives can become a support bot, a ticketing flow, or an internal assistant. It currently covers agent management, RAG retrieval, the workflow engine, tool calling, ASR, integrations, Playground, and audit traces, and is under active development.

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

Requirements: Docker 24+ and Docker Compose v2 (the only hard requirement); Python 3.10+ if you run the backend from source; Node.js 18+ for frontend development or manual builds.

```bash
git clone https://github.com/AbysenAI/aezab.git
cd aezab
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:8000`. The console first walks you through creating an admin account and logging in; if there's no model config or agent yet, a three-step first-run wizard appears automatically — pick an LLM provider card (Qwen / Zhipu / MiniMax / OpenAI / local Ollama / custom, just paste an API key) → test the connection → create an agent from a template in one click → jump into the Playground to test it.

Want a fully offline local model instead? Use `docker compose --profile local-llm up -d` — it additionally starts Ollama and downloads a small ~1GB local model (`qwen2.5:1.5b`) on first boot.

Hit a snag? See [`docs/troubleshooting_en.md`](docs/troubleshooting_en.md).

## Integrate

All API calls are authenticated with an `X-API-Key` header. Create a key from the console's **Integrations** page (scope it to `invoke`):

```bash
curl -X POST http://localhost:8000/api/v1/invoke \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your-key>" \
  -d '{"agent_id": "agent-id", "message": "I need to report a leaking kitchen pipe.", "tenant_id": "default"}'
```

For streaming, swap the path for `/api/v1/invoke/stream` (SSE; add `curl -N` to keep the stream open). The fastest no-code integration is the embeddable chat widget: drop a `<script>` tag into your page to get a floating chat bubble — a full runnable example is in `examples/widget-demo.html`.

The Integrations page also covers ASR transcription, Outbound Tools (agents calling your own backend APIs), Workflow Webhooks (HMAC-signed callbacks when a workflow completes or reaches a key step), and a Trace & Debug panel for developer troubleshooting.

The full interactive API reference is at `http://localhost:8000/docs` (Swagger UI); SDKs, webhook signature verification, and rate-limit/retry semantics are in [`docs/integration.md`](docs/integration.md).

## Use Cases

- Customer support: answer from product docs, service policies, and support manuals.
- Ticketing: repairs, applications, approvals, form collection, order lookup, and CRM updates.
- Internal operations: policy Q&A, process execution, system lookup, and cross-team routing.
- Industry deployments: run in a customer environment with their own models, data, and business APIs.

## Architecture

```text
aezab/
  server/            # FastAPI backend: api/ engine/ models/ schemas/ config.py
  console/src/       # React console: pages/ api.ts i18n/
  static/            # Built console assets + widget.js
  Dockerfile
  docker-compose.yml
  pyproject.toml
```

Aezab uses a conversation-first runtime: an agent's bound capabilities (knowledge, workflows, tools, agent delegation) are converted into function definitions, and the model decides which to call from conversation context — there's no separate intent router or classifier layer. Triggering behavior and tuning notes live in [`docs/configuration_en.md`](docs/configuration_en.md).

## Run From Source

Backend:

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e ".[rag]"

cp .env.example .env
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

The first time you open the console it also walks you through creating an admin account and shows the first-run setup wizard. The database and vector index are backed up automatically every 24 hours to `./data/backups/` — running from source and running via Docker share the same logic.

Build the frontend:

```bash
cd console
npm install
npm run build
cp -r dist/. ../static/
```

When syncing build output into `static/`, always merge-copy (as above) rather than emptying the target directory first — `static/widget.js` is hand-maintained and must survive console builds; see [`docs/development.md`](docs/development.md) for details.

Frontend development:

```bash
cd console
npm install
npm run dev
```

## Deployment

- SQLite is suitable for development and small trials; PostgreSQL, Redis, HTTPS, reverse proxying, and a backup strategy are recommended for production.
- API keys and model credentials should be stored in environment variables or a deployment secret manager.
- The database and vector index are backed up automatically every 24 hours to `./data/backups/`; database schema migrations run automatically at startup via Alembic — no manual `ALTER TABLE` needed.

The full production deployment checklist (single-process architecture constraints, SSE reverse-proxy config, widget security) is in [`docs/deployment.md`](docs/deployment.md).

## Example: a knowledge-base support bot in 5 minutes

Quick Start above just gets the service running; here's the full path from zero to wired into your business, using a "knowledge-base support bot" as the example.

1. **Clone and start**:

   ```bash
   git clone https://github.com/AbysenAI/aezab.git
   cd aezab
   cp .env.example .env
   docker compose up -d --build
   ```

   By default this only starts `server` and `redis` — lightweight, fast to boot, no model download. Open the console and the first-run wizard walks you through connecting a cloud model (recommended). Check readiness with `curl http://localhost:8000/health`.

   If you want a fully offline local model instead, use:

   ```bash
   docker compose --profile local-llm up -d
   ```

   This additionally starts `ollama`: on first boot, `ollama-init` automatically downloads a small ~1GB local model (`qwen2.5:1.5b`) so the console has a working model out of the box.

2. **Open the console and follow the wizard**: visit `http://localhost:8000`. On first open you'll be guided through creating an admin account and logging in. If there's no model config or agent yet, a three-step setup wizard appears automatically — pick an LLM provider card (Qwen / Zhipu / MiniMax / OpenAI / local Ollama / custom, just paste an API key) → test the connection → create an agent from a template in one click ("Knowledge Support", "Repair Ticket", or "Booking") → jump into the Playground to test it. Pick "Knowledge Support" here.

3. **Upload knowledge**: open the **Knowledge** page and upload your own FAQ / product docs for the agent you just created (TXT / MD / PDF / DOCX / CSV / XLSX supported — see the "Knowledge Upload Standard" section in [`docs/configuration_en.md`](docs/configuration_en.md)).

4. **Wire it into your site or your backend**: open **Integrations**, create an API key scoped to `invoke` only, then pick either path (or both):

   - **Web widget**: copy a `<script>` snippet into your website — it renders a floating chat bubble. A full runnable example is in `examples/widget-demo.html`.
   - **API integration (the primary path)**: call the Headless API straight from your own product's backend to embed the agent into an app, CRM, support desk, or any business process:

     ```bash
     curl -X POST "http://localhost:8000/api/v1/invoke" \
       -H "X-API-Key: <your-key>" -H "Content-Type: application/json" \
       -d '{"agent_id": "<agent_id>", "message": "Hello"}'
     ```

   SSE streaming, the Python/JS SDKs, file upload, and retry semantics are all covered in [`docs/integration.md`](docs/integration.md) (widget attribute reference in section 9).

> This is only one example of what the framework can do. Swap the template and the same flow becomes a repair-ticket bot or a booking assistant. Go further and you can compose workflows, tool calls, and multi-agent delegation into almost any business process — order lookup, internal knowledge assistants, approval flows, and more. Aezab is general-purpose agent infrastructure, not a customer-support product.

## Documentation

- [`docs/configuration_en.md`](docs/configuration_en.md) — model, agent, capability-triggering, and knowledge-upload configuration guide.
- [`docs/troubleshooting_en.md`](docs/troubleshooting_en.md) — common issues.
- [`docs/deployment.md`](docs/deployment.md) — production deployment checklist.
- [`docs/integration.md`](docs/integration.md) — SDK / API / webhook / widget integration details.
- [`docs/migrations.md`](docs/migrations.md) — database migrations (Alembic).
- [`docs/development.md`](docs/development.md) — dev conventions and local checks (for contributors; Chinese only).

## License

MIT
