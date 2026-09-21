# Contributing

Start with a reproducible task: what did the operator configure, what did the caller send, and what should the system have returned or executed? A screenshot of a graph alone is insufficient; its saved transitions must agree with execution.

Use Python 3.10+ and Node 20+; CI uses Python 3.11. Install `pip install -e '.[dev]' numpy faiss-cpu jieba`, then `npm ci --prefix console`. The offline suite uses a temporary SQLite database and fake provider responses; it does not need model credentials or embedding downloads.

```bash
python scripts/verify_offline.py
node tests/workflow_graph.test.cjs
npm --prefix console run build
```

CI runs the same commands. `scripts/verify_offline.py` lists the public tests explicitly so ignored local experiments cannot enter a normal regression run. When adding a public test, add it to that list and the allowlist in `.gitignore`. Install `.[rag]` as well when working on document parsing or local embedding models.

Keep frontend HTTP requests in `console/src/api.ts`. Add tenant ownership checks alongside new resource lookups. Model/tool text is data; it cannot authorize a tenant change, staff approval or external write. Add schema migrations when stored state changes, including a regression for an existing database. For workflow changes, test the selected destination and side-effect count, not just the final sentence.

Describe the concrete trigger and changed behavior in a PR. Include verification and any unresolved deployment constraint. Keep live-provider smoke tests opt-in, synthetic and separate from offline tests; record model/version and outcome without publishing credentials, customer prompts or response payloads. Do not commit `.env`, local databases, backups or private evaluation fixtures.

The [design review](docs/agent-engineering-2026-09.md) describes the project boundary. Focus contributions on making a bounded task easier to configure, execute, recover and inspect.
