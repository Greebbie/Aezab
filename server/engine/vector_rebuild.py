"""Vector index rebuild — shared core + auto-rebuild-on-mismatch trigger.

`rebuild_vector_index()` holds the actual rebuild logic (scan
`knowledge_chunks` -> re-embed -> write a fresh index) that used to live
inline in `server/api/vector_admin.py`'s `POST /rebuild` handler. That
endpoint is now a thin wrapper around this function so the same logic can
also run automatically in the background.

Why background auto-rebuild exists: `server/engine/vector_store.py` detects
a FAISS index whose dimension no longer matches the configured embedding
model (e.g. an admin switched embedding providers) and, historically, just
created a fresh EMPTY index and logged a warning telling an admin to call
`POST /vector-admin/rebuild` by hand. For non-technical operators that
warning is invisible — the agent just goes quietly dumb (vector search
returns nothing, no error surfaces to the user). `mark_dimension_mismatch()`
lets vector_store flag the problem without needing an event loop (it may run
from a sync/thread-guarded singleton-construction path), and
`maybe_auto_rebuild()` — called from `KnowledgeRetriever.retrieve()` on
every request — turns that flag into a one-shot background rebuild task the
very next time someone asks a question, self-healing within the time it
takes to re-embed the corpus.

Concurrency:
  - `rebuild_vector_index()` is guarded by `_rebuild_lock` (an `asyncio.Lock`
    used only to make the "is a rebuild already running" check-and-set
    atomic — it is NOT held for the duration of the rebuild, so it never
    blocks a concurrent caller; that caller instead gets back
    `{"status": "already_running"}` immediately). This covers both a manual
    `POST /vector-admin/rebuild` call landing mid-auto-rebuild and two
    auto-rebuild attempts racing each other.
  - `mark_dimension_mismatch()` / `needs_rebuild()` / the retry counter are
    guarded by `_state_lock` (a `threading.Lock`, never held across an
    `await`) since `mark_dimension_mismatch()` can be called from a sync
    context (see vector_store.py's local import + comment).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from sqlalchemy import select

from server.db import async_session
from server.models.knowledge import KnowledgeChunk

logger = logging.getLogger(__name__)

# ── Rebuild-in-progress guard ───────────────────────────────────────

_rebuild_lock = asyncio.Lock()
_rebuild_in_progress = False

# ── Dimension-mismatch flag + auto-retry budget ─────────────────────

_state_lock = threading.Lock()
_dimension_mismatch = False
_auto_retry_count = 0
_MAX_AUTO_RETRIES = 3

# Strong references to in-flight background auto-rebuild tasks — create_task
# only holds a WEAK reference internally, so an unreferenced task can be
# garbage-collected mid-await (same pattern as event_dispatcher.emit_event /
# summary_scheduler.schedule_summary_update).
_background_tasks: set[asyncio.Task] = set()


def mark_dimension_mismatch() -> None:
    """Signal that the FAISS index dimension no longer matches the active
    embedding model's dimension. Called from vector_store.py's index-load
    path, which may run under VectorStoreManager's singleton-construction
    threading.Lock rather than inside a running event loop — so this only
    flips a flag, it never touches asyncio or spawns a task itself.
    """
    global _dimension_mismatch
    with _state_lock:
        _dimension_mismatch = True


def needs_rebuild() -> bool:
    """Return whether a dimension mismatch is currently flagged."""
    with _state_lock:
        return _dimension_mismatch


async def maybe_auto_rebuild() -> None:
    """Fire-and-forget: if a mismatch was flagged and the auto-retry budget
    isn't exhausted, clear the flag and kick off a background rebuild.

    Safe to call unconditionally on every retrieve() — the common case (no
    mismatch flagged) is a single lock-guarded bool read, nanosecond-scale
    overhead.
    """
    global _dimension_mismatch, _auto_retry_count

    with _state_lock:
        if not _dimension_mismatch:
            return
        if _auto_retry_count >= _MAX_AUTO_RETRIES:
            # Give up auto-retrying so this branch (and its log line) doesn't
            # fire on every single retrieve() call. An admin now needs to
            # investigate and trigger POST /vector-admin/rebuild manually.
            # Reset the counter too: a future, unrelated mismatch (e.g. a
            # second embedding-model swap after the admin fixes the first
            # problem) should get its own full retry budget rather than
            # inheriting this exhausted one.
            _dimension_mismatch = False
            _auto_retry_count = 0
            logger.error(
                "vector_rebuild: auto-rebuild failed %d times in a row; "
                "giving up. Run POST /vector-admin/rebuild manually after "
                "investigating the underlying failure.",
                _MAX_AUTO_RETRIES,
            )
            return
        _dimension_mismatch = False

    task = asyncio.create_task(_run_auto_rebuild())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_auto_rebuild() -> None:
    """Task body for the auto-triggered rebuild. Never raises — failures are
    logged and re-flag the mismatch (bounded by `_MAX_AUTO_RETRIES`) so the
    next retrieve() call gets another attempt.
    """
    global _auto_retry_count, _dimension_mismatch

    try:
        result = await rebuild_vector_index()
        status = result.get("status")
        if status == "already_running":
            # Another rebuild (a manual API call, or a previous auto-attempt
            # still in flight) owns this cycle; its own success/failure path
            # is authoritative, so this attempt doesn't count either way.
            return
        if status == "error":
            raise RuntimeError(result.get("message", "unknown rebuild error"))

        with _state_lock:
            _auto_retry_count = 0
        logger.info(
            "vector_rebuild: auto-rebuild completed (%s chunks indexed in %ss)",
            result.get("chunks_indexed"), result.get("duration_s"),
        )
    except Exception as e:  # noqa: BLE001 - background task must never raise
        with _state_lock:
            _auto_retry_count += 1
            _dimension_mismatch = True  # retry on the next retrieve() call
        logger.error(
            "vector_rebuild: auto-rebuild attempt failed: %s", e, exc_info=True,
        )


# ── Shared rebuild core ──────────────────────────────────────────────


async def rebuild_vector_index() -> dict[str, Any]:
    """Rebuild the vector index from every row in `knowledge_chunks`.

    Returns a status dict:
      - {"status": "already_running"} if a rebuild is already in flight.
      - {"status": "error", "message": ...} on failure (vector store
        unavailable or the embed/write step raising).
      - {"status": "empty", "chunks_indexed": 0, "duration_s": ...} if there
        are no chunks to index.
      - {"status": "completed", "chunks_indexed": N, "duration_s": ...,
         "count": N, "index_type": ..., "message": ...} on success.

    Opens its OWN `async_session` (never a caller-supplied one) so this can
    run standalone from a background task with no request context, exactly
    like `event_dispatcher.dispatch_event` / `summary_scheduler`'s fold.
    """
    global _rebuild_in_progress

    async with _rebuild_lock:
        if _rebuild_in_progress:
            return {"status": "already_running"}
        _rebuild_in_progress = True

    try:
        return await _do_rebuild()
    finally:
        async with _rebuild_lock:
            _rebuild_in_progress = False


async def _do_rebuild() -> dict[str, Any]:
    # Local import: avoids a circular import (vector_store.py locally
    # imports THIS module to call mark_dimension_mismatch()).
    from server.engine.vector_store import get_vector_store

    t0 = time.perf_counter()

    vs = get_vector_store()
    if vs is None:
        return {"message": "Vector store not available", "status": "error"}

    async with async_session() as db:
        result = await db.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.content, KnowledgeChunk.domain)
        )
        rows = result.all()

    if not rows:
        return {
            "message": "No chunks to index",
            "status": "empty",
            "chunks_indexed": 0,
            "duration_s": round(time.perf_counter() - t0, 3),
        }

    batch = [
        {"chunk_id": row.id, "text": row.content, "domain": row.domain}
        for row in rows
    ]

    try:
        # For FAISS: reset to a fresh HNSW index before rebuilding.
        # For pgvector/other DB-backed stores: delete all existing vectors.
        if hasattr(vs, "_create_hnsw_index"):
            vs._create_hnsw_index()
        else:
            vs.delete([row.id for row in rows])

        vs.add_batch(batch)
        vs.save()
    except Exception as e:
        logger.error("vector_rebuild: rebuild failed: %s", e, exc_info=True)
        return {"message": f"Rebuild failed: {e}", "status": "error"}

    duration_s = round(time.perf_counter() - t0, 3)
    backend_type = type(vs).__name__
    return {
        "message": f"Index rebuilt with {len(batch)} vectors",
        "status": "completed",
        "count": len(batch),
        "chunks_indexed": len(batch),
        "duration_s": duration_s,
        "index_type": (
            "hnsw" if getattr(vs, "is_hnsw", False)
            else "pgvector" if "PgVector" in backend_type
            else "flat"
        ),
    }
