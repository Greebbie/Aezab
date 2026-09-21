"""Bounded invoke admission and SSE lifecycle under actual concurrent requests."""

import asyncio
import importlib

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from server.config import settings
from server.db import get_db
from server.engine import event_dispatcher, request_guard
from server.engine.agent_runtime import AgentRuntime
from server.schemas.invoke import InvokeRequest, InvokeResponse

invoke_api = importlib.import_module("server.api.invoke")


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(monkeypatch, db):
    monkeypatch.setattr(settings, "disable_auth", True)
    monkeypatch.setattr(settings, "max_concurrent_invokes", 2)
    monkeypatch.setattr(settings, "session_wait_timeout_seconds", 0.03)
    app = FastAPI()
    app.include_router(invoke_api.router)

    async def no_database():
        yield db

    app.dependency_overrides[get_db] = no_database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def request(session="one"):
    return {"agent_id": "agent", "session_id": session, "message": "run"}


@pytest.mark.parametrize("endpoint", ["/invoke", "/invoke/stream"])
async def test_capacity_returns_retryable_503_before_sse_headers(client, monkeypatch, endpoint):
    monkeypatch.setattr(settings, "max_concurrent_invokes", 1)
    started, release = asyncio.Event(), asyncio.Event()

    async def pipeline(self, req, event_cb=None):
        started.set()
        await release.wait()
        return InvokeResponse(session_id=req.session_id, trace_id="trace", short_answer="ok")

    monkeypatch.setattr(AgentRuntime, "invoke", pipeline)
    first = asyncio.create_task(client.post(endpoint, json=request()))
    await asyncio.wait_for(started.wait(), timeout=1)
    try:
        rejected = await asyncio.wait_for(client.post(endpoint, json=request("two")), timeout=1)
        assert rejected.status_code == 503
        assert rejected.headers["Retry-After"] == "1"
    finally:
        release.set()
        assert (await first).status_code == 200
    assert (await client.post(endpoint, json=request("three"))).status_code == 200


@pytest.mark.parametrize("endpoint", ["/invoke", "/invoke/stream"])
async def test_session_wait_is_bounded_and_releases_capacity(client, monkeypatch, endpoint):
    started, release = asyncio.Event(), asyncio.Event()

    async def pipeline(self, req, event_cb=None):
        started.set()
        await release.wait()
        return InvokeResponse(session_id=req.session_id, trace_id="trace", short_answer="ok")

    monkeypatch.setattr(AgentRuntime, "invoke", pipeline)
    first = asyncio.create_task(client.post(endpoint, json=request()))
    await asyncio.wait_for(started.wait(), timeout=1)
    try:
        rejected = await asyncio.wait_for(client.post(endpoint, json=request()), timeout=1)
        assert rejected.status_code == 429
        assert rejected.headers["Retry-After"] == "1"
        assert request_guard.active_invoke_count() == 1
    finally:
        release.set()
        await first
    assert request_guard.active_invoke_count() == 0
    assert request_guard.active_session_lock_count() == 0


async def test_cancelled_sync_request_releases_slot_and_session(client, monkeypatch):
    started = asyncio.Event()

    async def pipeline(self, req, event_cb=None):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(AgentRuntime, "invoke", pipeline)
    task = asyncio.create_task(client.post("/invoke", json=request()))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert request_guard.active_invoke_count() == 0
    assert request_guard.active_session_lock_count() == 0


async def test_cached_idempotent_retry_does_not_require_a_free_slot(client, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_invokes", 1)
    response = InvokeResponse(session_id="cached", trace_id="cached", short_answer="saved")
    request_guard.store_idempotent_response("default", "admission-replay", response.model_dump(), 60)
    async with request_guard.invoke_slot():
        replay = await client.post("/invoke", json=request(), headers={
            "Idempotency-Key": "admission-replay",
        })
    assert replay.status_code == 200
    assert replay.json()["short_answer"] == "saved"


async def test_sse_owns_and_closes_its_database_session(client, monkeypatch, db):
    closed = asyncio.Event()

    class StreamSession(AsyncSession):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            closed.set()

    async def pipeline(self, req, event_cb=None):
        assert self.db is not db
        await self.db.execute(text("SELECT 1"))
        return InvokeResponse(session_id=req.session_id, trace_id="trace", short_answer="ok")

    monkeypatch.setattr(invoke_api, "AsyncSession", StreamSession)
    monkeypatch.setattr(AgentRuntime, "invoke", pipeline)
    response = await client.post("/invoke/stream", json=request())
    assert response.status_code == 200
    assert closed.is_set()


async def test_unconsumed_sse_response_never_reserves_capacity(monkeypatch, db):
    monkeypatch.setattr(settings, "disable_auth", True)
    response = await invoke_api.invoke_stream(
        InvokeRequest(**request()), db=db, tenant_id="default",
    )
    assert request_guard.active_invoke_count() == 0
    assert request_guard.active_session_lock_count() == 0
    await response.body_iterator.aclose()


@pytest.mark.parametrize("fail_after_tokens", [False, True])
async def test_full_sse_queue_disconnect_cancels_producer_without_deadlock(
    monkeypatch, fail_after_tokens, db,
):
    monkeypatch.setattr(settings, "disable_auth", True)
    monkeypatch.setattr(settings, "sse_queue_maxsize", 2)
    send_started, disconnected, producer_cancelled = (asyncio.Event() for _ in range(3))
    produced = 0

    async def pipeline(self, req, event_cb=None):
        nonlocal produced
        try:
            for _ in range(3 if fail_after_tokens else 100):
                await event_cb("answer_delta", {"text": "token"})
                produced += 1
            if fail_after_tokens:
                raise RuntimeError("provider failed with a full queue")
            await asyncio.Event().wait()
        finally:
            producer_cancelled.set()

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            send_started.set()
            await asyncio.Event().wait()

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    monkeypatch.setattr(AgentRuntime, "invoke", pipeline)
    response = await invoke_api.invoke_stream(
        InvokeRequest(**request()), db=db, tenant_id="default",
    )
    serving = asyncio.create_task(response(
        {"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send,
    ))
    try:
        await asyncio.wait_for(send_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert produced <= 3  # One in send(), two in the queue.
    finally:
        disconnected.set()
        await asyncio.wait_for(serving, timeout=1)
    assert producer_cancelled.is_set()
    assert request_guard.active_invoke_count() == 0
    assert request_guard.active_session_lock_count() == 0


async def test_event_shutdown_drains_then_cancels_remaining_tasks(monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow_delivery(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(event_dispatcher, "dispatch_event", slow_delivery)
    event_dispatcher.emit_event("tenant", "workflow.completed", {})
    await asyncio.wait_for(started.wait(), timeout=1)
    await event_dispatcher.shutdown_event_tasks(timeout_seconds=0)
    assert cancelled.is_set()
    assert not event_dispatcher._background_tasks


@pytest.mark.parametrize("spec_version", ["2.0", "2.4"])
@pytest.mark.parametrize("completed", [False, True])
async def test_stalled_sse_delivery_expires_and_releases_its_resources(
    monkeypatch, db, caplog, spec_version, completed,
):
    monkeypatch.setattr(settings, "disable_auth", True)
    monkeypatch.setattr(settings, "sse_queue_maxsize", 1)
    monkeypatch.setattr(settings, "pipeline_timeout_seconds", 0.02)
    monkeypatch.setattr(invoke_api, "_SSE_DELIVERY_GRACE_SECONDS", 0.03)
    producer_finished, database_closed, send_started = (asyncio.Event() for _ in range(3))

    class StreamSession(AsyncSession):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            assert request_guard.active_invoke_count() == 1
            assert request_guard.active_session_lock_count() == 1
            database_closed.set()

    async def pipeline(self, req, event_cb=None):
        try:
            if not completed:
                for _ in range(100):
                    await event_cb("answer_delta", {"text": "token"})
            return InvokeResponse(session_id=req.session_id, trace_id="trace", short_answer="ok")
        finally:
            producer_finished.set()

    async def receive():
        # This client remains connected but never drains its response.
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.body":
            send_started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(invoke_api, "AsyncSession", StreamSession)
    monkeypatch.setattr(AgentRuntime, "invoke", pipeline)
    response = await invoke_api.invoke_stream(
        InvokeRequest(**request()), db=db, tenant_id="default",
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(response(
            {"type": "http", "asgi": {"spec_version": spec_version}}, receive, send,
        ), timeout=1)
    assert "SSE delivery deadline reached" in caplog.text
    assert send_started.is_set()
    assert producer_finished.is_set()
    assert database_closed.is_set()
    assert request_guard.active_invoke_count() == 0
    assert request_guard.active_session_lock_count() == 0
