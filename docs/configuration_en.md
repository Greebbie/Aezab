# Configuration Guide

> For readers who already have Aezab running and need to configure models, agents, capability
> triggering, and knowledge upload. Installation and quick start: root
> [`README.md`](../README.md). 中文版：[`docs/configuration.md`](./configuration.md)。

---

## Naming Note

Aezab is the current public brand name. `HlAB` (Headless AI Agent Builder) is the historical
codename — the environment variable prefix `AEZAB_` (with `HLAB_` kept as a backward-compatible
alias), the SDK package name `hlab-client`, and some code comments/file names still use HlAB.
They refer to the same project, not two different products.

## Core Concepts

| Concept | Description |
| --- | --- |
| Agent | Runtime unit for a business scenario. It includes prompts, model config, capability bindings, and runtime policy. |
| Capability | A set of resources an agent can use: knowledge, workflows, tools, and agent delegation. |
| Knowledge | Document and structured knowledge sources with upload, chunking, indexing, retrieval, and citations. |
| Workflow | Multi-step business process with field collection, file upload, validation, tool calls, and callbacks. |
| Tool | External HTTP API or built-in function exposed to the agent through function calling. |

## Model Configuration

Aezab requires at least one working LLM. The recommended path is to leave the LLM settings in
`.env` untouched and open the console instead — the first-run wizard connects a cloud model for
you, and the config it writes to the database always takes priority over `.env` defaults. You can
also manage multiple model configs from the console's Model Configs page.

Local Ollama (fully offline; start the `ollama` service first with
`docker compose --profile local-llm up -d`):

```bash
AEZAB_LLM_PROVIDER=openai_compatible
AEZAB_LLM_BASE_URL=http://ollama:11434/v1
AEZAB_LLM_MODEL=qwen2.5:1.5b
```

DashScope:

```bash
AEZAB_LLM_PROVIDER=dashscope
AEZAB_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AEZAB_LLM_API_KEY=sk-your-key
AEZAB_LLM_MODEL=qwen-flash
```

Other OpenAI-compatible providers (MiniMax, DeepSeek, vLLM, etc.) follow the same shape — just
swap `base_url`/`model`. Runtime priority: `agent-bound model config > tenant default model
config > .env default`.

## Configure an Agent

Recommended setup path:

1. Create an agent in **Agents** and set its name, description, system prompt, and model config.
2. Create a knowledge source in **Knowledge** and upload documents.
3. Bind available knowledge sources in **Agents -> Capabilities**.
4. Configure business processes in **Workflows** and bind them to the agent.
5. Register external APIs in **Tools** or **Integrations -> Outbound Tools** and bind them to the agent.
6. Test conversation, retrieval, workflows, tool calls, and traces in **Playground**.

**Agents -> Capabilities is the source of truth for runtime capability configuration.** Bindings
created from Integrations are shortcuts and write back to the same Capabilities model.

## Capability Triggering

Aezab uses a conversation-first runtime: bound capabilities are converted into function
definitions, and the model decides which to call from conversation context.

| Capability | Main trigger material |
| --- | --- |
| Knowledge | Source, domain, document content, retrieval results. |
| Workflow | Workflow name, description, step definitions, optional agent-specific instruction. |
| Tool | Tool name, description, parameter schema, response schema. |
| Agent delegation | Agent connection settings and target agent description. |

If triggering is unstable, adjust the capability's own name/description/schema first, rather than
relying on agent-specific instructions.

## Knowledge Upload Standard

Uploaded files are parsed into text, recursively split by `chunk_size`/`chunk_overlap`, then
indexed for BM25, vector search, and fast lookup. The default single-file limit is
`AEZAB_KNOWLEDGE_MAX_UPLOAD_MB=50`.

- TXT/MD: UTF-8/GB18030/UTF-16, one knowledge point per paragraph, `Question: ... Answer: ...`
  works well for FAQs.
- DOCX/PDF: use real text, not scanned images; OCR scanned files first.
- CSV/XLSX: keep a header row; tables are converted into `Field: Value` retrieval text, which
  works better than raw cell concatenation.
- Avoid uploading: encrypted PDFs, pivot-style spreadsheets, embedded attachments, or text that
  only exists inside images.

Before publishing an agent, verify recall in **Knowledge -> Retrieval Test** and final behavior in
**Playground**.
