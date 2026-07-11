"""Audit trace retention — deletes expired ``audit_traces`` rows on a
background schedule so an unattended self-hosted install doesn't slowly fill
its disk with an append-only audit log.

Self-hosted single-process deployments (see docs/deployment.md) have no
operator watching disk usage. ``server/engine/audit_logger.py`` persists
every pipeline event to the ``audit_traces`` table and nothing ever deletes
old rows, so after months of uptime the table — and the disk — grows without
bound; eventually SQLite write failures take the whole site down. This
module bounds that growth:

- ``purge_expired_audit_traces()`` — one-shot purge of rows older than
  ``settings.audit_retention_days`` days, deleted in batches to avoid a
  single long-running transaction against a live database.
- ``retention_scheduler_loop()`` — background task wired into
  ``server/main.py`` lifespan; runs the purge once every 24 hours.

Scope: this module ONLY purges ``audit_traces``. It deliberately does NOT
touch ``messages`` / ``conversation_sessions`` — those hold conversation
history, which is business data the operator may still need (support,
compliance, analytics), not a diagnostic/audit log. If conversation
retention is ever needed it should be a separate, explicitly-configured
policy, not bundled into this purge.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from server.config import settings
from server.db import async_session
from server.models.audit import AuditTrace

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5000

_RETENTION_INTERVAL_SECONDS = 24 * 3600


async def purge_expired_audit_traces(batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Delete `audit_traces` rows older than `settings.audit_retention_days`.

    Returns immediately (0 deleted) when `audit_retention_days <= 0` — that
    value means "retain forever", per `server/config.py`.

    Deletes in batches of `batch_size` rows: each batch is its own
    select-ids-then-delete-by-id transaction, so a table with millions of
    expired rows never holds one long-running transaction against a live
    database (a single unbounded `DELETE ... WHERE timestamp < cutoff` would
    lock the table for the whole run). Loops until a batch comes back
    smaller than `batch_size`, meaning nothing expired remains.
    """
    retention_days = settings.audit_retention_days
    if retention_days <= 0:
        return 0

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    total_deleted = 0

    async with async_session() as db:
        while True:
            result = await db.execute(
                select(AuditTrace.id)
                .where(AuditTrace.timestamp < cutoff)
                .limit(batch_size)
            )
            ids = [row[0] for row in result.all()]
            if not ids:
                break

            await db.execute(delete(AuditTrace).where(AuditTrace.id.in_(ids)))
            await db.commit()
            total_deleted += len(ids)

            if len(ids) < batch_size:
                break

    if total_deleted:
        logger.info(
            "retention: purged %d expired audit trace(s) older than %d day(s)",
            total_deleted, retention_days,
        )
    return total_deleted


async def retention_scheduler_loop() -> None:
    """Background task: run `purge_expired_audit_traces()` every 24 hours.

    Wired into server/main.py's lifespan via asyncio.create_task and
    cancelled on shutdown, mirroring server/engine/backup.py's
    `backup_scheduler_loop`. Never raises — a failed purge attempt (e.g. a
    momentarily locked database) is logged and the loop continues, so a
    transient failure doesn't silently disable all future purges. Returns
    immediately, without starting the loop, when `audit_retention_days <= 0`
    (retention disabled — retain forever).
    """
    if settings.audit_retention_days <= 0:
        logger.info("retention: scheduler disabled (audit_retention_days <= 0)")
        return

    while True:
        await asyncio.sleep(_RETENTION_INTERVAL_SECONDS)
        try:
            deleted = await purge_expired_audit_traces()
            logger.info("retention: scheduled purge removed %d audit trace(s)", deleted)
        except Exception as exc:  # noqa: BLE001 - background loop must never die
            logger.error("retention: scheduled purge failed: %s", exc, exc_info=True)
