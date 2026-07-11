# Troubleshooting Guide

> A self-service guide for non-technical users. 中文版：[`docs/troubleshooting.md`](./troubleshooting.md).
> Each item is organized as "Symptom → Cause → What to do". Production deployment checklist:
> [`docs/deployment.md`](./deployment.md). External integration: [`docs/integration.md`](./integration.md).

---

## 1. Conversation errors / keeps failing

**Symptom**: Playground, or an agent you've embedded elsewhere, keeps erroring out, timing out,
or just not responding.

**Start here**: open the **Health** page in the console, find the "LLM" card, and click
"Recheck" — this sends one real minimal request to the model to verify actual reachability, not
just whether a config exists. The card gives a plain-language result plus an action button:

| Result | Meaning | What to do |
|---|---|---|
| `healthy` | The model connection is fine | If conversations still fail, the problem isn't the model — check the database/vector-store/circuit-breaker cards on the same Health page |
| `auth_error` | The API key is invalid or expired | Click "Go to LLM Configs" on the card, generate a new key from your provider's console (Qwen, Zhipu, MiniMax, OpenAI, etc.), paste it in, and re-test |
| `rate_limited` | Rate limited or out of balance | Check your balance and rate limit on the provider's account page, then wait or top up before retrying |
| `unreachable` | Can't reach the model service address | Verify `AEZAB_LLM_BASE_URL` (or the Base URL field in the model config) — spelling, protocol (http/https), port. Common when a mainland-China host tries to reach an overseas API directly, or an internal machine has no route to the public internet; set up a proxy or switch providers if needed |
| `not_configured` | No default model is set | Create a config on the LLM Configs page and mark it default, or run the first-run setup wizard |
| `error` | Some other failure | The card's message includes the specific error; if unclear, go to LLM Configs and click "Test Connection" manually for the full error |

When configuring a model for the first time, you can also just use the first-run setup wizard —
step 2 is "Test Connection", which does the same check as the Health page.

---

## 2. First knowledge upload is very slow / looks stuck

**Symptom**: the first time you upload a document on the Knowledge page, the progress bar sits
there for a long time, or the server logs seem to go quiet.

**Cause**: the default local embedding model (`BAAI/bge-m3`) is roughly 2GB. The **first** time
it's used, it gets downloaded automatically from HuggingFace into a local cache
(`aezab-model-cache` volume / `~/.cache/huggingface`). After that first download, every request
is fast — nothing gets re-downloaded.

**What to do**:

- Confirm `AEZAB_HF_ENDPOINT=https://hf-mirror.com` is still set in your `.env` and hasn't been
  removed or changed — this is a China-reachable HuggingFace mirror; without it, the download can
  be very slow or fail outright on mainland-China networks.
- Watch `docker compose logs -f server` for progress (you'll see `Downloading`-style log lines)
  to confirm it's slow, not fully stuck.
- **Low-memory machines (< 4GB available)** can hit an out-of-memory kill while loading the
  embedding model (process killed, container unexpectedly restarts). Give the `server` container
  at least 4GB of memory if possible; if memory is genuinely tight, switch
  `AEZAB_EMBEDDING_PROVIDER` to a cloud embedding API (e.g. DashScope) instead of loading a model
  locally.

---

## 3. Retrieval returns nothing after switching the embedding model

**Symptom**: the knowledge base clearly has documents in it, but after changing
`AEZAB_EMBEDDING_MODEL`, Playground / the retrieval test suddenly returns nothing.

**Cause**: different embedding models produce vectors of different dimensions (e.g. bge-m3 is
1024-dim, the MiniLM family is 384-dim). After switching models, the FAISS index still holds
vectors at the old dimension, which no longer matches the new model — retrieval breaks entirely.

**What to do**: rebuild the index from the console's Settings / vector management panel, or call
it directly:

```bash
curl -X POST http://localhost:8000/api/v1/vector-admin/rebuild \
  -H "X-API-Key: <your-key>"
```

This regenerates the vector index from scratch using the `knowledge_chunks` already stored in the
database — you don't need to re-upload documents. Retrieval may be briefly unavailable during the
rebuild; for a modest document count this usually takes seconds to a few minutes.

---

## 4. Port already in use

**Symptom**: `docker compose up -d --build` or `python -m uvicorn ...` fails with something like
`address already in use` / `port is already allocated` / `[Errno 98] Address already in use`.

**Cause**: port 8000 is already taken by something else — possibly a previous Aezab process that
didn't exit cleanly, or an unrelated service.

**What to do**:

- **Docker Compose**: edit the `ports` mapping under the `server` service in
  `docker-compose.yml`, changing `"8000:8000"` to e.g. `"8080:8000"` (the left side is the host
  port; leave the container-side port as-is), then browse to `http://localhost:8080`.
- **Running from source**: change `--port 8000` to something else, e.g.
  `uvicorn server.main:app --host 0.0.0.0 --port 8080`.
- To find out what's holding the port: on Windows, `netstat -ano | findstr :8000`, then check the
  PID in Task Manager — it may just be an earlier Aezab process you forgot to stop.

---

## 5. Console won't open / keeps returning 401

**Symptom**: the console shows a blank page or connection error, or you keep getting bounced back
to the login page and every request returns 401.

**Depending on the exact symptom**:

- **Won't open at all (connection failed/timeout)**: first confirm the backend process/container
  is actually running — `docker compose ps` should show `server` as `Up`, or just
  `curl http://localhost:8000/health` and see if you get a response. No response? Check
  `docker compose logs -f server` for a startup error.
- **Opens fine but keeps returning 401 / bounces back to login**: your login session (JWT) has
  expired — just log in again. If the service was recently restarted and you have **not** set a
  fixed `AEZAB_SECRET_KEY`, it signs tokens with a key persisted in `./data/secret_key` — as long
  as that file survives a restart, users won't get logged out. If that file was deleted, or the
  deployment moved to a new machine without carrying over `./data/`, all existing tokens become
  invalid and everyone needs to log in again — this is expected, not a bug.
- **Forgot the admin password**: there is currently **no** built-in "forgot password"
  email/SMS flow — you need to edit the database directly. **Always back up
  `./data/aezab.db` first** (see section 7 below), then pick one:
  1. Open `./data/aezab.db` with a SQLite tool and delete the corresponding row from the `users`
     table, then restart the service. `POST /api/v1/auth/register` auto-promotes the very first
     registered user to admin — but only if the `users` table is now completely empty. If other
     users still exist, delete just your own row, then have another admin recreate your account
     with `POST /api/v1/auth/register-admin`.
  2. Reset the whole database (delete or replace `./data/aezab.db` with a fresh file) — this
     wipes all agents, knowledge, sessions, etc. Only use this in a test environment or if you're
     fine starting over.

---

## 6. Webhooks aren't arriving

**Symptom**: you configured a `url` on an event subscription, the workflow completed
successfully, but your receiving endpoint never gets a callback.

**Cause**: Aezab blocks webhook URLs that point at internal/loopback addresses (`localhost`,
`127.0.0.1`, `10.x.x.x`, `192.168.x.x`, and other private ranges) by default — this is an SSRF
(server-side request forgery) protection. Such URLs are rejected outright, and delivery is
skipped.

**What to do**:

- If your receiver genuinely runs on an internal network or the same host as Aezab (local
  testing, or co-located on the same private network), explicitly set
  `AEZAB_ALLOW_INTERNAL_WEBHOOKS=true` in `.env` and restart the service to allow internal
  addresses. **Not recommended to leave on permanently in production** — turn it off, or point at
  a publicly reachable address, once you're done testing.
  If the receiver is already a public address but still isn't getting hit, check that your
  signature verification isn't rejecting valid requests — a common mistake is verifying the
  signature against JSON that was parsed and re-serialized, instead of the raw request body
  bytes. Full details and example code: [`docs/integration.md`](./integration.md), section 7.
- Confirm the subscription's `events` field includes the event type you expect (or `["*"]` for
  everything), and that the subscription itself has `enabled` set to true.

---

## 7. Restoring data from a backup

**Symptom**: you need to roll back to a previous backup (accidental data loss, a bad upgrade, a
machine migration, etc.).

**What to do**:

1. Locate a backup — either a `aezab-backup-YYYYMMDD-HHMMSS.zip` file under
   `./data/backups/` (auto-generated every 24 hours, newest 7 kept by default), or a copy you
   previously downloaded from the console's Settings page.
2. **Stop the Aezab service** (`docker compose down`, or stop the `uvicorn` process).
3. Extract the zip and follow the included `RESTORE.txt`: copy the extracted `data/` folder's
   contents into your deployment's `./data/` directory, overwriting existing files.
   - PostgreSQL deployments: this backup does **not** include the database itself — only the
     FAISS vector index and local config files. Restore the database separately with
     `pg_dump`/`pg_restore` or your cloud provider's backup tooling.
4. **Restart the Aezab service**.

As a general practice, periodically copy files from `./data/backups/` somewhere off this server
(object storage, an off-site disk, etc.) — they live on the same disk as the database itself, so
a full disk failure takes the automatic backups down with it.

---

## 8. `/health` component reference

Visiting `http://localhost:8000/health` (without an LLM check) returns something like:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "components": {
    "database": { "status": "healthy" },
    "vector_store": { "status": "healthy", "backend": "faiss", "count": 128 },
    "circuit_breakers": { "status": "healthy", "total": 3, "open": 0 }
  }
}
```

| Component | Meaning | If unhealthy |
|---|---|---|
| `database` | DB connectivity (runs a `SELECT 1`) | `unhealthy` usually means a corrupted database file, permission issue, or an unreachable PostgreSQL host |
| `vector_store` | Whether the vector index file exists and how many vectors it holds | `not_initialized` just means no index has been built yet (normal before your first upload); `unhealthy` — check the error detail and, if needed, rebuild per section 3 |
| `circuit_breakers` | Circuit-breaker state for external dependencies (model APIs, tool APIs, etc.) | `degraded` with `open` > 0 means some external dependency has been failing repeatedly and got auto-tripped; it retries automatically after a cooldown, or you can proactively check the relevant model/tool config per section 1 |

The top-level `status` summarizes the worst component state: `ok` when everything is healthy,
`degraded` if anything is unhealthy.

**LLM connectivity is an optional extra check**, since it costs a real (billed) request — it's
not part of the default response, and only runs when you explicitly pass `check_llm=true` (the
console's Health page LLM card already does this automatically):

```bash
curl "http://localhost:8000/health?check_llm=true"
```

The result adds a `components.llm` field whose `status` matches the table in section 1
(`healthy` / `auth_error` / `rate_limited` / `unreachable` / `not_configured` / `error`).
Results are cached for 60 seconds so that frequent monitoring polls don't turn this endpoint
into a hidden model load test. To bypass the cache and force an immediate re-check (e.g. right
after fixing a key), add `force=true` — this is exactly what the console's "Recheck" button
does:

```bash
curl "http://localhost:8000/health?check_llm=true&force=true"
```
