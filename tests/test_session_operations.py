"""Operator history is complete, ordered, tenant scoped, and read-only."""
from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from server.api.sessions import router
from server.config import settings
from server.db import Base, get_db
from server.engine.request_guard import session_lock
from server.middleware.auth import get_current_user, get_tenant_id
from server.models.session import ConversationSession, Message


@pytest_asyncio.fixture
async def operations():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def database():
        async with maker() as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix="/sessions")
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: {"tenant_id": "team"}
    app.dependency_overrides[get_tenant_id] = lambda: "team"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, maker
    await engine.dispose()


async def seed(maker, *, session_id="case", tenant="team", state=None, rows=0):
    async with maker() as db:
        db.add(ConversationSession(
            id=session_id, agent_id="agent", user_id="customer", tenant_id=tenant,
            status="active", workflow_state=state, message_count=rows // 2,
        ))
        # Equal timestamps deliberately exercise the deterministic ID tiebreak.
        for index in range(rows):
            db.add(Message(
                id=f"{session_id}-{index:05}", session_id=session_id,
                role="user" if index % 2 == 0 else "assistant", content=f"message-{index}",
                created_at=datetime(2026, 9, 22),
            ))
        await db.commit()


@pytest.mark.asyncio
async def test_latest_window_and_paging_reach_messages_after_500(operations):
    client, maker = operations
    await seed(maker, rows=505)
    latest = (await client.get("/sessions/case/messages", params={"latest": True, "limit": 50})).json()
    assert latest["total"] == 505
    assert latest["offset"] == 455
    assert [row["content"] for row in latest["items"]] == [f"message-{i}" for i in range(455, 505)]
    older = (await client.get("/sessions/case/messages", params={"offset": 405, "limit": 50})).json()
    assert older["items"][-1]["content"] == "message-454"
    # Existing API callers still receive oldest-first without latest=true.
    first = (await client.get("/sessions/case/messages", params={"limit": 2})).json()
    assert [row["content"] for row in first["items"]] == ["message-0", "message-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["paused_for_review", "await_retry", "waiting_input"])
async def test_detail_and_list_report_real_workflow_state_without_advancing(operations, status):
    client, maker = operations
    state = {"status": status, "workflow_id": "workflow", "current_step_index": 3}
    await seed(maker, state=state, rows=2)
    detail = (await client.get("/sessions/case")).json()
    assert detail["agent_id"] == "agent"
    assert detail["user_id"] == "customer"
    assert detail["status"] == detail["workflow_status"] == status
    listing = (await client.get("/sessions/")).json()
    assert listing["items"][0]["status"] == status
    async with maker() as db:
        assert (await db.get(ConversationSession, "case")).workflow_state == state


@pytest.mark.asyncio
async def test_stored_response_card_and_trace_are_returned(operations):
    client, maker = operations
    await seed(maker)
    card = {"step_name": "Request", "step_type": "collect", "prompt": "Describe the issue",
            "fields": [{"name": "issue", "label": "Issue"}], "current_step": 1, "total_steps": 2}
    async with maker() as db:
        db.add(Message(
            id="response", session_id="case", role="assistant", content="Describe the issue",
            trace_id="trace-123", citations=[{"source_id": "guide"}],
            metadata_={"workflow_card": card, "workflow_status": "waiting_input",
                       "metadata": {"mode": "workflow"}},
        ))
        await db.commit()
    response = (await client.get("/sessions/case/messages")).json()["items"][0]
    assert response["workflow_card"] == card
    assert response["workflow_status"] == "waiting_input"
    assert response["trace_id"] == "trace-123"
    assert response["citations"] == [{"source_id": "guide"}]


@pytest.mark.asyncio
async def test_other_tenant_cannot_inspect_or_continue_a_session(operations):
    client, maker = operations
    await seed(maker, tenant="other", rows=2)
    assert (await client.get("/sessions/case")).status_code == 404
    assert (await client.get("/sessions/case/messages", params={"latest": True})).status_code == 404
    assert (await client.get("/sessions/")).json()["items"] == []


@pytest.mark.asyncio
async def test_empty_latest_page_and_terminal_status(operations):
    client, maker = operations
    await seed(maker)
    async with maker() as db:
        session = await db.get(ConversationSession, "case")
        session.status = "completed"
        await db.commit()
    assert (await client.get("/sessions/case")).json()["status"] == "completed"
    page = (await client.get("/sessions/case/messages", params={"latest": True})).json()
    assert page["offset"] == 0
    assert page["items"] == []


@pytest.mark.asyncio
async def test_delete_waits_for_active_invoke_and_does_not_remove_rows_on_timeout(operations, monkeypatch):
    client, maker = operations
    await seed(maker, rows=2)
    monkeypatch.setattr(settings, "session_wait_timeout_seconds", 0.01)
    async with session_lock("case"):
        response = await client.delete("/sessions/case")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
        assert (await client.get("/sessions/case/messages")).json()["total"] == 2
    assert (await client.delete("/sessions/case")).status_code == 204
    assert (await client.get("/sessions/case")).status_code == 404
