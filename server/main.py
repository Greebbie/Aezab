"""FastAPI application entry point."""

from __future__ import annotations

import logging
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from server.api.agent_capabilities import router as agent_capabilities_router
from server.api.agent_connections import router as agent_connections_router
from server.api.agent_skills import router as agent_skills_router
from server.api.agent_templates import router as agent_templates_router
from server.api.agents import router as agents_router
from server.api.asr import router as asr_router
from server.api.audit import router as audit_router
from server.api.auth import router as auth_router
from server.api.backup import router as backup_router
from server.api.files import router as files_router
from server.api.invoke import router as invoke_router
from server.api.knowledge import router as knowledge_router
from server.api.llm_configs import router as llm_configs_router
from server.api.mock_tools import router as mock_tools_router
from server.api.performance import router as performance_router
from server.api.sessions import router as sessions_router
from server.api.skills import router as skills_router
from server.api.subscriptions import router as subscriptions_router
from server.api.tools import router as tools_router
from server.api.vector_admin import router as vector_admin_router
from server.api.workflows import router as workflows_router
from server.config import settings
from server.middleware.auth import enforce_rate_limit, require_scope

logger = logging.getLogger(__name__)

# Ensure data directory exists for SQLite
os.makedirs("./data", exist_ok=True)

_project_root = Path(__file__).resolve().parent.parent
STATIC_DIR = _project_root / "static"
if not STATIC_DIR.is_dir():
    # Fallback: serve from console/dist (development / pre-built frontend)
    STATIC_DIR = _project_root / "console" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    import contextlib

    from server.db import engine
    import server.models  # noqa: F401 – register all models
    from server.db_migrate import ensure_schema
    from server.engine.backup import backup_scheduler_loop
    from server.engine.retention import retention_scheduler_loop

    # Initialize default runtime config (balanced preset)
    from server.runtime_config import runtime_config
    from server.performance_presets import PRESETS
    runtime_config.update(PRESETS["balanced"])
    runtime_config.set("active_preset", "balanced")

    # Pre-initialize jieba to avoid ~1s cold start on first request
    try:
        import jieba
        jieba.initialize()
    except ImportError:
        pass

    # Schema bootstrap/migration — see server/db_migrate.py and
    # docs/migrations.md. Replaces the old bare create_all() + hand-rolled
    # ALTER TABLE workaround with real Alembic-managed migrations.
    await ensure_schema()

    # Zero-config automatic backups (server/engine/backup.py). Cancelled on
    # shutdown below; backup_scheduler_loop() itself returns immediately
    # without looping when backup_interval_hours <= 0 (disabled).
    backup_task = asyncio.create_task(backup_scheduler_loop())

    # Audit trace retention (server/engine/retention.py). Cancelled on
    # shutdown below; retention_scheduler_loop() itself returns immediately
    # without looping when audit_retention_days <= 0 (disabled).
    retention_task = asyncio.create_task(retention_scheduler_loop())

    yield

    backup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await backup_task
    retention_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await retention_task
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

def _resolve_cors_settings(cors_origins: str) -> dict[str, Any]:
    """Compute allow_origins/allow_credentials for CORSMiddleware.

    allow_origins=["*"] combined with allow_credentials=True is invalid per
    the Fetch/CORS spec — browsers reject that exact combination outright —
    yet it was the previous hardcoded default whenever cors_origins was left
    at its wildcard default. Force credentials off in that case; an explicit
    origin list keeps credentials enabled as before.
    """
    if cors_origins == "*":
        return {"allow_origins": ["*"], "allow_credentials": False}
    return {"allow_origins": cors_origins.split(","), "allow_credentials": True}


app.add_middleware(
    CORSMiddleware,
    allow_methods=["*"],
    allow_headers=["*"],
    **_resolve_cors_settings(settings.cors_origins),
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inject X-Request-ID into every request/response for end-to-end tracing."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        # Store on request state so handlers can access it
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


app.add_middleware(RequestIDMiddleware)

prefix = settings.api_prefix

# Scope/rate-limit gates are applied here (router-include level) rather
# than inside individual api/*.py modules, so those files stay untouched.
# See server/middleware/auth.py: require_scope, enforce_rate_limit.
_invoke_deps = [Depends(require_scope("invoke")), Depends(enforce_rate_limit)]
_manage_deps = [Depends(require_scope("manage"))]

app.include_router(invoke_router, prefix=prefix, tags=["invoke"], dependencies=_invoke_deps)
app.include_router(asr_router, prefix=prefix + "/asr", tags=["asr"])
app.include_router(agents_router, prefix=prefix + "/agents", tags=["agents"], dependencies=_manage_deps)
app.include_router(workflows_router, prefix=prefix + "/workflows", tags=["workflows"], dependencies=_manage_deps)
app.include_router(knowledge_router, prefix=prefix + "/knowledge", tags=["knowledge"], dependencies=_manage_deps)
app.include_router(tools_router, prefix=prefix + "/tools", tags=["tools"], dependencies=_manage_deps)
app.include_router(audit_router, prefix=prefix + "/audit", tags=["audit"])
app.include_router(mock_tools_router, prefix=prefix + "/mock-tools", tags=["mock-tools"])
app.include_router(llm_configs_router, prefix=prefix + "/llm-configs", tags=["llm-configs"], dependencies=_manage_deps)
app.include_router(performance_router, prefix=prefix + "/performance", tags=["performance"])
app.include_router(vector_admin_router, prefix=prefix + "/vector-admin", tags=["vector-admin"])
app.include_router(skills_router, prefix=prefix + "/skills", tags=["skills"], dependencies=_manage_deps)
app.include_router(agent_skills_router, prefix=prefix + "/agents", tags=["agent-skills"], dependencies=_manage_deps)
app.include_router(agent_connections_router, prefix=prefix + "/agent-connections", tags=["agent-connections"], dependencies=_manage_deps)
app.include_router(agent_capabilities_router, prefix=prefix + "/agents", tags=["agent-capabilities"], dependencies=_manage_deps)
app.include_router(agent_templates_router, prefix=prefix + "/agent-templates", tags=["agent-templates"], dependencies=_manage_deps)
app.include_router(auth_router, prefix=prefix + "/auth", tags=["auth"])
app.include_router(sessions_router, prefix=prefix + "/sessions", tags=["sessions"])
app.include_router(files_router, prefix=prefix + "/files", tags=["files"])
app.include_router(subscriptions_router, prefix=prefix + "/subscriptions", tags=["subscriptions"], dependencies=_manage_deps)
# backup_router carries its own require_role("admin") dependency (see
# server/api/backup.py) — no _manage_deps needed here.
app.include_router(backup_router, prefix=prefix + "/backups", tags=["backups"])


@app.get("/health")
async def health(check_llm: bool = False, force: bool = False):
    """System health check with component status.

    `check_llm` is opt-in and defaults to False: `/health` is polled
    frequently by monitoring systems, and an LLM reachability check costs a
    real (billed) request, so it never runs unless a caller explicitly asks
    for it (e.g. the console's Health page). `force` bypasses the 60s cache
    in server.engine.llm_health — see that module's docstring.
    """
    status = {"status": "ok", "version": "0.1.0", "components": {}}

    # Check database
    try:
        from server.db import engine
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        status["components"]["database"] = {"status": "healthy"}
    except Exception as e:
        status["components"]["database"] = {"status": "unhealthy", "error": str(e)}
        status["status"] = "degraded"

    # Check vector store without initializing embedding models. Heavy readiness
    # checks live under /api/v1/vector-admin/*.
    try:
        if settings.vector_store == "faiss":
            index_path = settings.faiss_index_path
            sidecar_path = index_path + ".ids.json"
            vector_count = None
            if os.path.exists(sidecar_path):
                with open(sidecar_path, "r") as f:
                    data = json.load(f)
                vector_count = len([cid for cid in data.get("ids", []) if cid])
            if os.path.exists(index_path):
                status["components"]["vector_store"] = {
                    "status": "healthy",
                    "backend": "faiss",
                    "count": vector_count,
                }
            else:
                status["components"]["vector_store"] = {
                    "status": "not_initialized",
                    "backend": "faiss",
                }
        else:
            status["components"]["vector_store"] = {
                "status": "configured",
                "backend": settings.vector_store,
            }
    except Exception as e:
        status["components"]["vector_store"] = {"status": "unhealthy", "error": str(e)}

    # Check circuit breakers
    try:
        from server.engine.circuit_breaker import circuit_breaker
        cb_status = circuit_breaker.get_all_status()
        open_circuits = [c for c in cb_status if c.get("state") == "open"]
        status["components"]["circuit_breakers"] = {
            "status": "degraded" if open_circuits else "healthy",
            "total": len(cb_status),
            "open": len(open_circuits),
        }
    except Exception:
        pass

    # LLM reachability — opt-in only (see docstring above). Never lets a
    # broken/misconfigured LLM take the whole health check down to
    # "unhealthy"; at most this downgrades an otherwise-"ok" status to
    # "degraded", matching the other component checks above.
    if check_llm:
        try:
            from server.engine.llm_health import check_llm_health
            llm_status = await check_llm_health(force=force)
            status["components"]["llm"] = llm_status
            if llm_status.get("status") != "healthy" and status["status"] == "ok":
                status["status"] = "degraded"
        except Exception as e:
            status["components"]["llm"] = {"status": "error", "message": f"LLM 健康检查失败：{e}"}
            if status["status"] == "ok":
                status["status"] = "degraded"

    return status


# ── Serve frontend SPA (production) ─────────────────
# Uses a custom ASGI app mounted at "/" so it is evaluated AFTER all API
# routes — this avoids the catch-all @app.get("/{path:path}") problem that
# intercepts /api/* paths (including FastAPI's trailing-slash redirects).
if STATIC_DIR.is_dir():
    from starlette.types import Receive, Scope, Send

    _index_html = STATIC_DIR / "index.html"
    _static_files = StaticFiles(directory=str(STATIC_DIR))

    class _SPAStaticFiles:
        """Serve static files; fall back to index.html for SPA routing."""

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await _static_files(scope, receive, send)
                return
            try:
                await _static_files(scope, receive, send)
            except Exception:
                # File not found → serve index.html (SPA client-side routing)
                scope["path"] = "/index.html"
                await _static_files(scope, receive, send)

    app.mount("/", _SPAStaticFiles())
