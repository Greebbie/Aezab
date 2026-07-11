"""Schema bootstrap / migration entry point.

Replaces the old bare `Base.metadata.create_all()` lifespan call, which only
ever ADDED new tables and could never apply a column-level ALTER -- customer
upgrades broke on the first schema change that wasn't a brand-new table
(Wave 2 already had to hand-roll a one-off `ALTER TABLE ... ADD COLUMN` in
server/main.py to work around exactly this). `ensure_schema()` replaces both
that hand-rolled migration and the bare `create_all` call.

Three cases, distinguished by inspecting the live database at startup:
  1. Fresh DB (no application tables at all): the fast dev path -- just
     `create_all` (much cheaper than replaying every migration table-by-table
     against an empty DB) followed by `alembic stamp head` so Alembic's own
     bookkeeping (the `alembic_version` table) agrees with what `create_all`
     just produced.
  2. Existing DB with application tables but no `alembic_version` table
     (every deployment that predates this wave, i.e. everything upgrading
     from pre-Wave-5): the DB is first HEALED to the Wave 5 baseline
     (`alembic/versions/0001_baseline.py`), then stamped. Healing
     replicates exactly what the old lifespan did on every boot --
     `create_all` (idempotent; only creates tables that are missing, e.g.
     a pre-Wave-3 DB that lacks `event_subscriptions`) plus the legacy
     `ALTER TABLE agents ADD COLUMN llm_config_id ...` patch, swallowing
     the already-exists failure exactly like the old code did (covers DBs
     whose agents table predates that column). Only then does
     `alembic stamp head` mark it current -- stamping without healing
     would freeze any missing-table/column gap forever, because Alembic
     would consider the schema up to date and never revisit it.
     Migrations apply normally from this point forward.
  3. `alembic_version` present: a real `alembic upgrade head`, applying any
     migrations newer than what's already recorded.

After whichever of the three branches ran, `ensure_schema()` also runs a
fourth, branch-independent heal step: `_migrate_plaintext_llm_keys()`
encrypts any `llm_configs.api_key` values still stored in plaintext (rows
written before `server.engine.secrets_store` existed). It is idempotent --
already-encrypted rows are skipped -- and failures are logged rather than
raised, so a broken heal never blocks startup.

The sync Alembic `command.*` entry points are run via `asyncio.to_thread`
because `alembic/env.py`'s online-mode path calls `asyncio.run(...)` itself
-- invoking that directly from `ensure_schema` (already running inside the
app's event loop, e.g. the FastAPI lifespan) would raise "asyncio.run()
cannot be called from a running event loop". `asyncio.to_thread` gives it a
fresh thread with no event loop of its own.

See docs/migrations.md for the full workflow (including how to autogenerate
a new revision after a model change).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.db import Base, engine
import server.models  # noqa: F401 - registers every model on Base.metadata;
# required so `Base.metadata.create_all()` below sees the full schema even
# when ensure_schema() is invoked before anything else has imported the
# individual model modules (e.g. a standalone script, or this module's own
# test suite run in isolation).

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    """Build an Alembic Config purely in Python -- no alembic.ini file read,
    so this never touches the app's Python logging configuration at
    startup/request time. Only `script_location` is needed to run
    `command.stamp` / `command.upgrade` programmatically; `alembic/env.py`
    itself resolves the database URL from `server.config.settings`."""
    cfg = Config()
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    return cfg


def _inspect_state(conn: Connection) -> tuple[bool, bool]:
    """Sync helper run via `connection.run_sync`. Returns
    (has_application_tables, has_alembic_version)."""
    inspector = inspect(conn)
    table_names = set(inspector.get_table_names())
    has_alembic_version = "alembic_version" in table_names
    has_application_tables = bool(table_names - {"alembic_version"})
    return has_application_tables, has_alembic_version


async def ensure_schema() -> None:
    """Bring the configured database's schema up to date at startup.

    Genuine failures (e.g. a broken migration) propagate -- silently
    continuing with a stale/broken schema would be worse than a loud
    startup failure.
    """
    async with engine.connect() as conn:
        has_application_tables, has_alembic_version = await conn.run_sync(_inspect_state)

    cfg = _alembic_config()

    if not has_application_tables:
        logger.info("ensure_schema: fresh database -> create_all + stamp head")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await asyncio.to_thread(command.stamp, cfg, "head")

    elif not has_alembic_version:
        logger.info(
            "ensure_schema: existing pre-Alembic database -> heal to the "
            "Wave 5 baseline (create_all + legacy llm_config_id patch), "
            "then stamp head"
        )
        # Heal step 1: create any tables this DB is missing (a deployment
        # that last booted on an older build may lack newer tables, e.g.
        # pre-Wave-3 DBs have no `event_subscriptions`). create_all is
        # idempotent -- it never touches tables that already exist.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Heal step 2: the legacy Wave 2 column patch the old lifespan
        # applied on every boot. Covers DBs whose agents table predates
        # llm_config_id; the failure on an already-present column is
        # swallowed exactly like the old code did (explicit
        # commit/rollback so the swallowed failure never leaves a dead
        # transaction to be committed on context exit).
        async with engine.connect() as conn:
            try:
                await conn.execute(text(
                    "ALTER TABLE agents ADD COLUMN llm_config_id VARCHAR(36) "
                    "REFERENCES llm_configs(id) ON DELETE SET NULL"
                ))
                await conn.commit()
                logger.info("ensure_schema: added llm_config_id column to agents table")
            except Exception:
                await conn.rollback()  # Column already exists -- expected on most databases
        await asyncio.to_thread(command.stamp, cfg, "head")

    else:
        logger.info("ensure_schema: alembic_version present -> upgrade head")
        await asyncio.to_thread(command.upgrade, cfg, "head")

    # One-time (idempotent) heal: encrypt any llm_configs.api_key values
    # still stored in plaintext (rows written before secrets_store existed).
    # Runs after every branch above so it also catches fresh databases that
    # were seeded with plaintext keys by a script/fixture, not just upgrades.
    await _migrate_plaintext_llm_keys()


async def _migrate_plaintext_llm_keys() -> None:
    """Session-scoped wrapper around
    `server.engine.secrets_store.migrate_plaintext_llm_keys`, bound to this
    module's (monkeypatchable) `engine` rather than `server.db.async_session`
    so tests that swap in a tmp-file engine are fully isolated from the real
    database. Failures are logged, not raised -- a broken key migration must
    never block application startup; the affected rows simply stay
    plaintext until the next successful boot."""
    from server.engine.secrets_store import migrate_plaintext_llm_keys

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            migrated = await migrate_plaintext_llm_keys(session)
            if migrated:
                logger.info("ensure_schema: encrypted %d plaintext llm_configs.api_key row(s)", migrated)
    except Exception as exc:  # noqa: BLE001 - never block startup on this heal step
        logger.error("ensure_schema: llm_configs.api_key encryption heal failed: %s", exc)
