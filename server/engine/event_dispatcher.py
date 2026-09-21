"""Outbound event dispatcher — HMAC-signed webhook delivery, fire-and-forget.

Workflow (and future) event sources call `emit_event(tenant_id, event_type,
payload)` — a plain, synchronous, non-raising call that schedules delivery
in the background via `asyncio.create_task`. The caller's success/failure
path is never affected by subscriber delivery outcomes.

`dispatch_event` does the actual work: it opens its OWN `async_session`
(never the caller's — the caller's transaction may already be committed,
rolled back, or mid-flight by the time delivery runs) and POSTs to every
enabled subscription for the tenant whose `events` list contains the event
type (or the wildcard "*").
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from server.config import env_str
from server.db import async_session
from server.models.subscription import EventSubscription

# Strong references to in-flight background delivery tasks. The event loop
# only holds a weak reference to a task created via create_task, so without
# this a task suspended in retry backoff can be garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()


async def shutdown_event_tasks(timeout_seconds: float = 5.0) -> None:
    """Drain current deliveries, then cancel and await those beyond the deadline.

    This only owns process-local tasks; it cannot recover callbacks after a crash.
    """
    tasks = set(_background_tasks)
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _background_tasks.difference_update(tasks)


class WebhookTargetBlockedError(ValueError):
    """Raised when a subscription URL resolves to a blocked (internal) target."""


def _allow_internal_webhooks() -> bool:
    # Self-hosted single-tenant deployments may legitimately post to internal
    # services; opt in explicitly. Default posture blocks SSRF targets.
    # Env: AEZAB_ALLOW_INTERNAL_WEBHOOKS (legacy HLAB_ accepted as fallback).
    return env_str("ALLOW_INTERNAL_WEBHOOKS", "false").lower() in ("1", "true", "yes")


def check_webhook_url(url: str) -> None:
    """Validate configuration; delivery also pins the checked IP per attempt."""
    _resolve_webhook_target(url)


def _resolve_webhook_target(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebhookTargetBlockedError("url must start with http:// or https://")
    host = parsed.hostname
    if not host:
        raise WebhookTargetBlockedError("url has no host")
    if parsed.username is not None or parsed.password is not None:
        raise WebhookTargetBlockedError("url must not contain credentials")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise WebhookTargetBlockedError(f"cannot resolve host: {host}") from e

    addresses = []
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise WebhookTargetBlockedError("resolver returned an invalid address") from exc
        if not _allow_internal_webhooks() and (
            not ip.is_global or ip.is_multicast or ip.is_reserved
            or (isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local)
        ):
            raise WebhookTargetBlockedError(
                f"url resolves to a blocked internal address ({addr})"
            )
        addresses.append(str(ip))
    if not addresses:
        raise WebhookTargetBlockedError("host has no resolved addresses")
    return addresses[0]

logger = logging.getLogger(__name__)

# Delivery policy: 10s per-attempt timeout, 3 retries beyond the first
# attempt (1s/2s/4s backoff) — same exponential shape used elsewhere in this
# codebase (see workflow_executor.WEBHOOK_MAX_RETRIES).
DELIVERY_TIMEOUT_S = 10.0
MAX_RETRIES = 3
RETRY_BACKOFF_S = 1.0


def _sign(secret: str, raw_body: bytes) -> str:
    """HMAC-SHA256 over the exact bytes sent, hex-encoded, `sha256=` prefixed."""
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _deliver_one(sub: EventSubscription, event_type: str, body_bytes: bytes) -> None:
    """POST to a single subscription with retry. Never raises — logs on
    final exhaustion."""
    headers = {
        "Content-Type": "application/json",
        "X-HlAB-Event": event_type,
        "X-HlAB-Signature": _sign(sub.secret, body_bytes),
    }

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            address = await asyncio.wait_for(
                asyncio.to_thread(_resolve_webhook_target, sub.url), timeout=DELIVERY_TIMEOUT_S,
            )
            original = httpx.URL(sub.url)
            # Connect to the validated address; preserve HTTP Host and TLS
            # certificate/SNI verification for the configured hostname.
            target = original.copy_with(host=address)
            attempt_headers = {**headers, "Host": original.netloc.decode("ascii")}
            async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_S, trust_env=False, follow_redirects=False) as client:
                resp = await client.post(target, content=body_bytes, headers=attempt_headers,
                                         extensions={"sni_hostname": original.raw_host.decode("ascii")})
            if resp.is_success:
                return
            last_error = RuntimeError(f"HTTP {resp.status_code}")
        except WebhookTargetBlockedError as e:
            logger.warning("Skipping delivery to blocked webhook %s: %s", sub.id, e)
            return
        except Exception as e:  # noqa: BLE001 - any transport failure is retryable
            last_error = e

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_BACKOFF_S * (2 ** attempt))

    logger.warning(
        "Event delivery failed: subscription=%s url=%s event=%s attempts=%d error=%s",
        sub.id, sub.url, event_type, MAX_RETRIES + 1, last_error,
    )


async def dispatch_event(tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Deliver `event_type` to every enabled, matching subscription for `tenant_id`.

    Opens its own DB session — never reuses a caller-supplied session.
    Never raises: DB lookup failures and delivery failures are logged only.
    """
    try:
        async with async_session() as db:
            result = await db.execute(
                select(EventSubscription).where(
                    EventSubscription.tenant_id == tenant_id,
                    EventSubscription.enabled.is_(True),
                )
            )
            subs = [
                s for s in result.scalars().all()
                if event_type in (s.events or []) or "*" in (s.events or [])
            ]
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to load event subscriptions for tenant %s: %s", tenant_id, e)
        return

    if not subs:
        return

    body = {
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")

    for sub in subs:
        try:
            await _deliver_one(sub, event_type, body_bytes)
        except Exception as e:  # noqa: BLE001 - defense in depth, _deliver_one shouldn't raise
            logger.error(
                "Unexpected error delivering event %s to subscription %s: %s",
                event_type, sub.id, e,
            )


def emit_event(tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Fire-and-forget: schedule `dispatch_event` as a background task.

    Synchronous and non-raising by design so callers in hot paths (e.g.
    WorkflowExecutor) never need to await or handle delivery failures.
    """
    async def _run() -> None:
        try:
            await dispatch_event(tenant_id, event_type, payload)
        except Exception as e:  # noqa: BLE001 - dispatch_event already catches broadly; belt & suspenders
            logger.error("emit_event: dispatch_event raised unexpectedly: %s", e)

    try:
        task = asyncio.get_running_loop().create_task(_run())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except RuntimeError:
        # No running event loop in this context — never happens from async
        # request handlers, but guard anyway so emit_event never raises.
        logger.warning("emit_event: no running event loop; dropping event %s", event_type)
