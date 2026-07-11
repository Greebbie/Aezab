"""Commercial reliability primitives for /invoke: per-session serialization
and client-driven idempotency.

Both mechanisms are **per-process, in-memory** — they hold no state outside
this Python process. That is sufficient for a single-worker deployment or a
sticky-session (same client always routed to the same worker) multi-worker
deployment. If the platform ever moves to a non-sticky multi-worker or
multi-instance deployment, both primitives need a shared backend (e.g. a
Redis lock for `session_lock`, a Redis TTL cache for the idempotency store)
to keep the same guarantees across processes. That is the documented
upgrade path — not implemented in this wave.

Design mirrors the opportunistic-purge rate limiter in
server/middleware/auth.py: a plain dict guarded by a short-held
`threading.Lock` (never held across an `await`), so bookkeeping stays cheap
and non-blocking relative to the async request path.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import OrderedDict
from typing import Any

# ── C-T1: per-session serialization ─────────────────────────────────
#
# Two concurrent /invoke (or /invoke/stream) calls for the SAME session_id
# must run serially — otherwise their message writes / workflow_state
# mutations can interleave. Calls for DIFFERENT sessions (or a brand-new
# session with session_id=None, nothing to race with yet) proceed fully
# concurrently.
#
# Each entry is (asyncio.Lock, refcount). refcount tracks how many
# in-flight callers currently hold or are waiting on that session's lock;
# the entry is deleted once it drops to zero so the dict does not grow
# unboundedly across the process lifetime.

_session_locks_guard = threading.Lock()
_session_locks: dict[str, list[Any]] = {}  # session_id -> [asyncio.Lock, refcount]


def active_session_lock_count() -> int:
    """Number of session_ids currently tracked (held or awaited). Test hook."""
    with _session_locks_guard:
        return len(_session_locks)


@contextlib.asynccontextmanager
async def session_lock(session_id: str | None):
    """Serialize concurrent invocations for the same session_id.

    Usage:
        async with session_lock(req.session_id):
            response = await runtime.invoke(req)

    A None session_id (brand-new session — nothing to race with) is a no-op:
    no lock is created, no bookkeeping happens.
    """
    if not session_id:
        yield
        return

    with _session_locks_guard:
        entry = _session_locks.get(session_id)
        if entry is None:
            entry = [asyncio.Lock(), 0]
            _session_locks[session_id] = entry
        entry[1] += 1
        lock = entry[0]

    try:
        async with lock:
            yield
    finally:
        with _session_locks_guard:
            current = _session_locks.get(session_id)
            if current is not None and current[0] is lock:
                current[1] -= 1
                if current[1] <= 0:
                    del _session_locks[session_id]


# ── C-T2: client idempotency ─────────────────────────────────────────
#
# Guards against a client accidentally retrying the same logical request
# (e.g. a flaky network causing a double POST) by caching the InvokeResponse
# for a client-supplied `Idempotency-Key`, scoped per tenant so two tenants
# can never collide on the same key. This is a best-effort, per-process
# cache — not a distributed guarantee. It pairs with (but is distinct from)
# the workflow-level idempotency_key, which guards duplicate submissions on
# the workflow side.
#
# Bounded by _IDEMPOTENCY_MAX_ENTRIES (oldest-entry eviction, OrderedDict
# acting as a simple LRU) and by TTL (entries older than their stored
# expiry are purged lazily on access).

_IDEMPOTENCY_MAX_ENTRIES = 1000

_idempotency_guard = threading.Lock()
# (tenant_id, key) -> (expires_at_monotonic, response_dict)
_idempotency_cache: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()


def _purge_expired_locked(now: float) -> None:
    """Must be called while holding _idempotency_guard."""
    expired = [k for k, (expires_at, _resp) in _idempotency_cache.items() if expires_at <= now]
    for k in expired:
        del _idempotency_cache[k]


def get_idempotent_response(tenant_id: str, key: str | None) -> dict | None:
    """Return the cached response dict for (tenant_id, key), or None on
    miss/expiry/no key supplied."""
    if not key:
        return None

    now = time.monotonic()
    with _idempotency_guard:
        _purge_expired_locked(now)
        cache_key = (tenant_id, key)
        entry = _idempotency_cache.get(cache_key)
        if entry is None:
            return None
        expires_at, response_dict = entry
        if expires_at <= now:
            del _idempotency_cache[cache_key]
            return None
        _idempotency_cache.move_to_end(cache_key)
        return response_dict


def store_idempotent_response(tenant_id: str, key: str | None, response_dict: dict, ttl_s: float) -> None:
    """Cache response_dict under (tenant_id, key) for ttl_s seconds.

    No-op if key is falsy (idempotency is opt-in per request).
    """
    if not key:
        return

    now = time.monotonic()
    with _idempotency_guard:
        _purge_expired_locked(now)
        cache_key = (tenant_id, key)
        _idempotency_cache[cache_key] = (now + ttl_s, response_dict)
        _idempotency_cache.move_to_end(cache_key)
        while len(_idempotency_cache) > _IDEMPOTENCY_MAX_ENTRIES:
            _idempotency_cache.popitem(last=False)  # evict oldest entry
