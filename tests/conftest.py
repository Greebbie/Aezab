"""Offline tests get a disposable database, never the developer's .env database."""
import asyncio
import gc
import os
from pathlib import Path
import tempfile

import pytest
import pytest_asyncio

_runtime = tempfile.TemporaryDirectory(prefix="aezab-pytest-")
_directory = Path(_runtime.name)
os.environ["AEZAB_DATABASE_URL"] = f"sqlite+aiosqlite:///{(_directory / 'tests.db').as_posix()}"
os.environ["AEZAB_SECRET_KEY"] = "offline-test-secret-not-for-production"
os.environ["AEZAB_DISABLE_AUTH"] = "true"
os.environ["AEZAB_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
os.environ["AEZAB_LLM_API_KEY"] = "offline-unused"
os.environ["AEZAB_TYPESAFE_ENABLED"] = "false"
os.environ["AEZAB_FAISS_INDEX_DIR"] = (_directory / "vectors").as_posix()


@pytest_asyncio.fixture(autouse=True)
async def close_background_work():
    """Finish task/session cleanup on the loop that created the async resources."""
    yield
    from sqlalchemy.ext.asyncio import close_all_sessions
    from server.db import engine
    from server.engine.event_dispatcher import shutdown_event_tasks
    from server.engine.summary_scheduler import shutdown_summary_tasks

    await shutdown_event_tasks()
    await shutdown_summary_tasks()
    await close_all_sessions()
    # Tests use separate event loops; do not carry idle aiosqlite connections
    # and their worker-thread callbacks into a later loop.
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def isolated_database():
    from server.db import Base, engine
    import server.models  # noqa: F401 - register all model tables

    async def create():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create())
    yield

    async def close():
        from sqlalchemy.ext.asyncio import close_all_sessions
        await close_all_sessions()
        await engine.dispose()

    asyncio.run(close())
    # Release cyclic SQLite driver references before Windows deletes the files.
    gc.collect()
    _runtime.cleanup()
