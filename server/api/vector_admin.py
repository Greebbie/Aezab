"""Vector store administration API."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from fastapi import APIRouter, Depends

from server.middleware.auth import get_current_user
from server.config import settings
from server.engine.vector_rebuild import rebuild_vector_index
from server.engine.vector_store import EmbeddingModel, get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

# ── Background warmup state machine ────────────────────────────────
# The embedding model (local provider) is no longer pre-downloaded during the
# Docker build; the console's Knowledge page triggers a download on demand via
# POST /warmup. That call must return immediately (the download itself can
# take minutes), so the actual load runs in a background asyncio task and
# this module-level state is polled by GET /model-status. Guarded by a
# short-held threading.Lock (never held across an `await`), mirroring the
# lock discipline used by server/engine/summary_scheduler.py.
_warmup_lock = threading.Lock()
_warmup_state: dict[str, Any] = {"status": "idle", "error": None}

# Strong references to in-flight background tasks so they are not garbage
# collected mid-await (asyncio.create_task only keeps a weak reference).
_background_tasks: set[asyncio.Task] = set()


def _is_model_cached(model_name: str) -> bool:
    """Best-effort check for whether `model_name` is already in the local
    HuggingFace hub cache, so /model-status can report "ready" without
    forcing a load. Returns False (never raises) if huggingface_hub isn't
    installed or the cache can't be scanned."""
    try:
        from huggingface_hub import scan_cache_dir
    except ImportError:
        return False
    try:
        cache_info = scan_cache_dir()
    except Exception:
        return False
    return any(repo.repo_id == model_name for repo in cache_info.repos)


@router.get("/model-status")
async def model_status():
    """Show embedding model configuration and download/load state without
    forcing a download. Status values: ready / not_downloaded / downloading
    / error. API-based embedding providers have no local download step and
    always report "ready"."""
    embedding = EmbeddingModel._instance
    configured_model = settings.embedding_model

    with _warmup_lock:
        warmup_status = _warmup_state["status"]
        warmup_error = _warmup_state["error"]

    if settings.embedding_provider != "local":
        status = "ready"
        message = None
    elif embedding is not None:
        status = "ready"
        message = None
    elif warmup_status == "downloading":
        status = "downloading"
        message = "Downloading embedding model in the background."
    elif warmup_status == "error":
        status = "error"
        message = warmup_error
    elif _is_model_cached(configured_model):
        status = "ready"
        message = None
    else:
        status = "not_downloaded"
        message = None

    return {
        "provider": settings.embedding_provider,
        "configured_model": configured_model,
        "configured_dimension": settings.embedding_dim,
        "hf_endpoint": settings.hf_endpoint,
        "env_hf_endpoint": os.environ.get("HF_ENDPOINT"),
        "loaded": embedding is not None,
        "loaded_model": getattr(embedding, "_model_name", "") if embedding else "",
        "loaded_dimension": embedding.dimension if embedding else None,
        "vector_store": settings.vector_store,
        "status": status,
        "message": message,
    }


@router.post("/warmup")
async def warmup_vector_store():
    """Kick off embedding model download/load in the background and return
    immediately. Repeated calls while a download is in flight are idempotent
    no-ops. See _run_warmup for the actual (blocking) load, which runs off
    the event loop via asyncio.to_thread."""
    with _warmup_lock:
        if EmbeddingModel._instance is not None:
            _warmup_state["status"] = "ready"
            _warmup_state["error"] = None
            return {"status": "started"}
        if _warmup_state["status"] == "downloading":
            return {"status": "started"}
        _warmup_state["status"] = "downloading"
        _warmup_state["error"] = None

    task = asyncio.create_task(_run_warmup())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "started"}


async def _run_warmup() -> None:
    """Background task body for POST /warmup. Never raises — failures are
    recorded in _warmup_state for /model-status to surface."""
    try:
        vs = await asyncio.to_thread(get_vector_store)
    except Exception as exc:  # noqa: BLE001 - background task must never raise
        logger.exception("Vector warmup failed")
        with _warmup_lock:
            _warmup_state["status"] = "error"
            _warmup_state["error"] = str(exc)
        return

    with _warmup_lock:
        if vs is None:
            _warmup_state["status"] = "error"
            _warmup_state["error"] = (
                "Embedding model or vector store could not be initialized. "
                "Check network connectivity to the configured HuggingFace "
                "endpoint (AEZAB_HF_ENDPOINT)."
            )
        else:
            _warmup_state["status"] = "ready"
            _warmup_state["error"] = None


@router.get("/stats")
async def vector_stats():
    """Get vector index statistics (count, dimension, memory estimate)."""
    vs = get_vector_store()
    if vs is None:
        return {
            "index_count": 0,
            "dimension": 0,
            "memory_usage_mb": 0.0,
            "status": "unavailable",
        }
    return {
        "index_count": vs.count(),
        "dimension": vs.dimension,
        "memory_usage_mb": round(vs.memory_usage_mb(), 3) if hasattr(vs, "memory_usage_mb") else 0.0,
        "index_type": "hnsw" if getattr(vs, "is_hnsw", False) else "pgvector" if type(vs).__name__ == "PgVectorStore" else "flat",
        "status": "ready",
    }


@router.post("/rebuild")
async def rebuild_index():
    """Trigger a full vector index rebuild from all knowledge chunks.

    Thin wrapper — the actual scan/re-embed/write logic lives in
    server.engine.vector_rebuild.rebuild_vector_index so the identical
    rebuild can also run automatically in the background when
    vector_store.py detects an embedding-dimension mismatch (see
    vector_rebuild.maybe_auto_rebuild, triggered from
    KnowledgeRetriever.retrieve()). Response shape is unchanged: this always
    includes "message"/"status", and "count"/"index_type" on success.
    """
    return await rebuild_vector_index()


@router.get("/health")
async def vector_health():
    """Check whether the vector store is initialized and operational."""
    vs = get_vector_store()
    if vs is None:
        return {
            "initialized": False,
            "backend": "faiss",
            "status": "unavailable",
        }
    backend_name = type(vs).__name__
    return {
        "initialized": True,
        "backend": "pgvector" if "PgVector" in backend_name else "faiss",
        "index_count": vs.count(),
        "dimension": vs.dimension,
        "index_type": "hnsw" if getattr(vs, "is_hnsw", False) else "pgvector" if "PgVector" in backend_name else "flat",
        "status": "healthy",
    }
