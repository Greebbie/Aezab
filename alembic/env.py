"""Alembic async migration environment for Aezab.

See docs/migrations.md for the full workflow (autogenerating a revision
after a model change, how upgrades run automatically on startup, the
sqlite batch-mode caveat, and a postgres note).

Key points:
  - `target_metadata` is `server.db.Base.metadata` with `server.models`
    imported first so every model is registered on it (autogenerate diffs
    against whatever tables/columns are attached to this metadata).
  - The database URL always comes from `server.config.settings.database_url`
    (AEZAB_DATABASE_URL / legacy HLAB_DATABASE_URL) — never a value baked
    into alembic.ini — so migrations always target the same database the
    running app would use.
  - Migrations run through an async engine (`connection.run_sync(...)`).
    `run_migrations_online` calls `asyncio.run(...)` itself; callers that
    are already inside a running event loop (see server/db_migrate.py) must
    invoke the sync `alembic.command.*` entry points via
    `asyncio.to_thread(...)` so this gets a fresh thread with no loop of its
    own — calling it directly from a running loop would raise
    "asyncio.run() cannot be called from a running event loop".
  - `render_as_batch=True` whenever the target dialect is sqlite: sqlite
    can't ALTER a column in place, so Alembic's "batch mode" recreates the
    table under the hood. This must be on for any future ALTER-style
    migration to work against the sqlite database this project ships with
    by default.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Make `server` importable regardless of the working directory alembic is
# invoked from (repo root is the parent of this alembic/ directory).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.config import settings  # noqa: E402
from server.db import Base  # noqa: E402
import server.models  # noqa: E402,F401 - registers every model on Base.metadata

# Alembic Config object, providing access to values within alembic.ini.
config = context.config

# Interpret the config file for Python logging (skipped when embedded —
# server/db_migrate.py's programmatic calls pass no config file).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Always target the app's configured database, not whatever (if anything)
# is hardcoded in alembic.ini.
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL, no DB connection)."""
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode against an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
