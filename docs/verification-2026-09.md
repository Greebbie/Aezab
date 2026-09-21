# September 2026 verification record

Run date: 22 September 2026. This records local verification of the working-tree changes, not a production certification. Tests use isolated synthetic data; no customer ticket, refund or other external business write was performed.

## Open-source release checks

The final public Python suite passed **356 tests** from a separate copy containing only Git-visible source files. The copy used a fresh Python 3.11 environment with the declared dependency ranges, including FastAPI 0.141.1. Run the same suite with `python scripts/verify_offline.py`; its 19 test modules are shared by the contributor instructions and GitHub Actions. Private experiments and local credentials are excluded from this suite and from the release copy.

The additional regressions cover template model ownership, truthful request receipts, strict tool schemas, agent-specific workflow models, semantic-validation failure, colliding and long function names, console routing and API redirects. They reproduced concrete defects before the fixes. A workflow now uses its agent's configured model for extraction and validation, rejects failed semantic validation, and preserves the declared input schema when passing idempotency information. Repair and booking templates record request details without claiming that an external system accepted them.

Browser checks used an isolated authenticated server and a local OpenAI-compatible provider fixture. They covered first-admin registration, failed model tests and recovery, three stale-test races, template creation, a complete repair intake, business-table creation and CSV import, query-tool binding, and opening the newly created agent in Playground. The manual wizard was also checked for empty names, back navigation, retained edits and a single final create request. Clearing the model after creation persisted `null` through an update without creating a duplicate agent. Completing setup while already on the Playground route refreshed the agent list and invoked the newly created agent. Desktop and 820px views were inspected. These checks verify application behavior, not model quality; no paid provider calls were made in this release check.

The source build is served directly from `console/dist`; the packaged container serves `static`. Browser navigation can fall back to the console entry point, while missing assets and unknown API routes still return 404. API trailing-slash redirects were verified with and without a reverse-proxy root path and in a mounted ASGI application.

The clean source copy passed `npm ci`, the workflow graph contract test, and the TypeScript/Vite build. npm reported zero known vulnerabilities; Vite still reports the existing bundle-size warning. Both Compose configurations validated. The final Docker image built successfully and started against a new data volume as UID/GID `10001:10001`, with all capabilities dropped and `no-new-privileges`. Real HTTP checks passed for account bootstrap, authentication, typed data import/query, rejected unauthorized requests, query limits and credential redaction. Restart retained the records, API key, migration `0002` and decryptable encrypted tool credential. The container served the final frontend bundle, returned 404 for missing assets and unknown API routes, and preserved the API slash redirect. Local reports are `data/verification/container-release-smoke-initial.json`, `container-release-smoke-restart.json` and `container-release-routing.json`. The real PostgreSQL checks described below were rerun and again passed all 27 assertions.

The JavaScript SDK was installed and built from its source directory, packed, and imported without a global TypeScript installation. The Python SDK wheel was built in isolation and imported successfully. Both packages include the MIT license. The candidate-file audit checks actual local provider credentials, private-key patterns, database artifacts and private project references without printing their contents. Local databases, reports, screenshots, environments and dependency directories remain ignored. No commit or push was performed.

## Business data and operating boundaries: follow-up verification

Before the release checks above, the expanded public suite passed **314 tests** on Windows/Python 3.11, with SQLite worker-thread warnings treated as errors. It covers both business-data modes, migration `0002`, source-schema/import concurrency, API-key and role boundaries, first-account bootstrap races, credential redaction, real file-SQLite contention, session restoration, bounded summary batches, callback DNS pinning, and bounded SSE admission/delivery. Provider calls in this suite are synthetic.

The graph contract test, TypeScript/Vite builds, default and PostgreSQL Compose validation, changed-file Ruff checks and `git diff --check` passed. `npm audit --audit-level=moderate` reported zero known vulnerabilities. The bundle-size warning remains; GitHub-hosted CI has not run in this session.

A separate isolated **real PostgreSQL 17** check passed 27 assertions: fresh platform schema and restart at `0002`; local JSON records persisted and filtered on PostgreSQL; read-only business views with typed parameters and server-bound tenant constraints; blocked SQL injection and tenant spoofing; database role restrictions; actual read-only transaction state; five-second statement timeout without retries; connection closure; and a real ToolGateway/Agent capability path with a fake LLM. This verifies database execution without a paid model request. It does not validate pgvector performance, HA or a production workload. The sanitized local report is `data/verification/business_postgres_smoke.json`.

Browser checks cover creating a business-data source, importing CSV, editing/deleting records, required filters, disabled sources, exact query results, refresh persistence, generated-tool navigation and PostgreSQL credentials remaining blank on edit. Session checks cover latest/older history, racing transcript requests, restoring a paused workflow, explicit continuation, saved completion cards and refresh. Desktop and 820px layouts were inspected; screenshots are local under `output/playwright/`.

The Docker build failure recorded in the earlier phase below was resolved by removing the unnecessary Debian compiler installation and using CPU Torch by default. A complete image built successfully and ran as UID/GID `10001:10001` with dropped capabilities; CPU Torch reported `2.9.1+cpu` and no CUDA. The local `docker images` size was 2.24 GB. This is a local image observation, not a controlled cross-product size benchmark. The final rebuild includes the frontend, widget and migrations. Real HTTP checks passed for admin bootstrap/login, API-key authentication, data-source creation, typed import/query, required-filter and row-limit rejection, hidden credentials, unauthorized paid-health rejection, console and widget delivery. Restart retained migration `0002`, records, working API keys and decryptable encrypted tool tokens. The first immediate post-restart request ran before readiness and was disconnected; the rerun after startup passed. Local reports are `data/verification/container-business-smoke-initial.json` and `container-business-smoke-restart.json`.

Independent Python and database reviews found additional defects rather than relying only on the initial passing tests: API-key telemetry held a SQLite write lock, concurrent bootstrap could create two first administrators, and schema edits could race imports. Each was fixed and covered by a reproducing regression. Runtime review also found a connected client that could stop consuming SSE forever; a whole-response deadline now releases the producer, session lock and admission slot.

Final code review verified two more browser regressions using synthetic intercepted requests: return to session A after session B fails to restore, and switching language while a restored session awaits an invoke response. Python independently reviewed the source-write locking and SSE deadline changes, with 37 focused tests and type checks passing. No paid model calls were added in this follow-up. The candidate-file credential scan found neither supplied provider key in the files being prepared for review; real credentials remain in ignored local storage.

## Earlier Jev/workflow verification

The sections below preserve the earlier evidence and limitations as recorded before the business-data work. The follow-up results above supersede their Docker/PostgreSQL status, not their live model measurements.

### Offline contracts and build

The seven public Python suites passed **134 tests** on Windows/Python 3.13. They cover Jev response validation and failure paths, workflow routing and confirmation, context budgets and summary watermarks, tenant/source boundaries, model configuration ownership and delegation. FAISS selector tests use real FlatIP and HNSW indexes; pgvector query scoping uses a test double, not a live PostgreSQL server.

```bash
pip install -e '.[dev]' numpy faiss-cpu jieba
python -m pytest tests/test_decision_service.py tests/test_workflow_decisions.py tests/test_workflow_graph.py tests/test_context_budget.py tests/test_runtime_resource_scope.py tests/test_agent_capability_descriptions.py tests/test_agent_runtime_memory.py -q
node tests/workflow_graph.test.cjs
npm ci --prefix console
npm --prefix console run build
npm audit --prefix console --audit-level=moderate
docker compose --env-file .env.example config --quiet
```

The graph contract test, TypeScript/Vite build, Compose validation, changed-file Ruff checks and `git diff --check` passed. Dependency audit reported **zero known vulnerabilities** with the lockfile included in this change. The frontend uses React 18, React Router 7.18.4 and Vite 6.4.3. Existing `datetime.utcnow()` deprecation warnings remain in Python tests; Vite reports a large-bundle warning. The GitHub Actions workflow adds an independent Python 3.11/Node 20 check but has not been run on GitHub in this session.

### Actual MiniMax and Jev calls

The live harness uses `MiniMax-M2.7` for conversation and native function calling, and `jev-1.13.0` for closed-set decisions. It reads credentials from environment variables and writes local reports under ignored `data/verification/`. Credentials are not included in the script, workflow example, images or tracked reports.

The first run made **4 MiniMax + 7 Jev requests**, all HTTP 200. Four Chinese/English repair and billing cases routed to the expected destinations. An unrelated request and an equally mixed request selected `other` and reached clarification. Missing input was rejected locally without a Jev call. Plain conversation also worked.

That first run exposed a meaningful failure: the native workflow reached the correct terminal step, but an extra model round embellished its local receipt into a promise that staff would arrange a visit. The original report was reassessed as **8/9**, rather than treating a correct route as a correct final answer. The runtime now returns workflow prompts and execution receipts directly, and stops later tools in that same model batch.

The focused post-fix run made **3 MiniMax + 1 Jev requests**, all HTTP 200, and passed **2/2** cases: ordinary conversation and native workflow invocation. The workflow assertion checks native capability selection, extracted input, accepted decision, completion status, and exact equality between the configured receipt, workflow card and final answer:

> TEST_REPAIR_RECEIPT: 已归入维修分支，尚未创建外部工单。

The reusable command is `python scripts/verify_agent_workflows.py`; `--runtime-only` selects the focused rerun. It caps each run at six MiniMax and twelve Jev requests. See [setup and evaluation guidance](jev-decisions.md).

These are integration smoke cases, not an accuracy benchmark. Simple Jev cases returned very high confidence; this does not establish calibration. The initial seven Jev HTTP calls took approximately 0.80–0.99 seconds each in this environment, while adapter wall time including client/proxy setup was approximately 3.05–3.66 seconds. Those figures are not service-level commitments or a comparison against other models.

The live rerun also exposed a test-harness shutdown race with background SQLite work. The harness now waits for event and summary tasks before disposing the database. That teardown change was verified with an offline HTTP replay, without additional paid calls; the live report retains the original cleanup warning and identifies the offline follow-up separately.

The chat smoke predicate was subsequently tightened to reject runtime error/degraded metadata and require a successful, case-local MiniMax response with a model ID. Eight offline predicate cases passed. The earlier live report has the successful provider call and answer, but did not retain all response metadata; the additional metadata rejection is reported as an offline check, not a new live run.

### Browser and deployment checks

Browser verification uses an isolated SQLite database and server. The actual console was exercised for workflow navigation, opening the graph, editing decision fields and thresholds, viewing branch rules, dragging a failure connection, saving/reopening it, and deleting a default route while preserving conditional routes. Dragging alone does not write to the API. Stable step IDs survive renaming.

Desktop and narrow-screen views were checked after the final dependency build, with no browser runtime errors observed. All five test nodes and their branches fit the 820px view; the step editor's Save button stays visible at 480px. Screenshots are local artifacts under `output/playwright/`, not checked-in product data.

Fresh database startup and restart were checked through the real FastAPI lifespan: Alembic migration `0001`, healthy `/health`, accessible standalone widget, and HTTP 401 for unauthenticated agent management. Compose resolves to only the application service by default; Ollama remains opt-in.

Two actual Docker builds were attempted with Docker Engine 29.1.3. Both compiled the frontend inside the Node 20 image, but backend image construction failed while downloading Debian build packages: the default attempt lost its connection to `deb.debian.org:80`; the retry through the existing host proxy returned HTTP 400 for one GCC package. The retry selected the existing CPU Torch build option. No complete backend image or running-container smoke test is claimed. Migration/widget packaging corrections are source- and lifespan-verified; a full image build remains to be repeated on a working package-download path.

## Remaining boundaries

- External helpdesk integration has not been verified.
- `human_review` is a requester-resumable pause, not a staff approval queue.
- Process-local session locks and deduplication do not establish durable, horizontally scaled execution or exactly-once external effects.
- Real PostgreSQL business queries and platform storage were checked in the follow-up phase. GPU/Ollama inference, a long-running load test, pgvector performance and customer-specific labeled routing evaluation remain unverified.
- The build lockfile improves frontend reproducibility; Python dependencies still use supported ranges rather than a fully pinned deployment lock.
