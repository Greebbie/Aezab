"""Runtime transaction boundaries against independent file-SQLite connections."""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.db import Base
from server.engine import audit_logger
from server.engine.agent_runtime import AgentRuntime
from server.engine.audit_logger import AuditLogger
from server.engine.workflow_executor import WorkflowStepResult
from server.models.agent import Agent
from server.models.audit import AuditTrace
from server.models.session import ConversationSession, Message
from server.models.skill import Skill
from server.schemas.invoke import Citation, InvokeRequest, InvokeResponse, WorkflowCard


@pytest_asyncio.fixture
async def runtime_sessions(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'runtime.db').as_posix()}",
        connect_args={"timeout": 0.1},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=100")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(audit_logger, "async_session", sessions)
    async with sessions() as db:
        db.add(Agent(id="agent", name="Test agent", tenant_id="default"))
        await db.commit()
    yield sessions
    await engine.dispose()


@pytest.mark.parametrize("existing_session", [False, True])
async def test_model_wait_does_not_block_another_session_write(
    runtime_sessions, monkeypatch, existing_session,
):
    if existing_session:
        async with runtime_sessions() as db:
            db.add(ConversationSession(
                id="first", agent_id="agent", user_id="anonymous", tenant_id="default",
            ))
            await db.commit()

    model_started = asyncio.Event()
    release_model = asyncio.Event()

    async def waiting_pipeline(self, agent, session, req, trace_id, audit, event_cb=None):
        # A workflow mutates state and then queries the next step/tool before
        # waiting on a provider. The SELECT must not flush that state early.
        session.workflow_state = {"status": "in_progress"}
        await self.db.execute(select(Agent.id))
        model_started.set()
        await release_model.wait()
        await self._save_message(session.id, "user", req.message, trace_id)
        await self._save_session(session)
        return InvokeResponse(session_id=session.id, trace_id=trace_id, short_answer="ok")

    monkeypatch.setattr(AgentRuntime, "_invoke_conversational", waiting_pipeline)
    async with runtime_sessions() as first_db, runtime_sessions() as second_db:
        task = asyncio.create_task(AgentRuntime(first_db).invoke(InvokeRequest(
            agent_id="agent", session_id="first", message="start",
        )))
        try:
            await asyncio.wait_for(model_started.wait(), timeout=2)
            second_db.add(ConversationSession(
                id="second", agent_id="agent", user_id="anonymous", tenant_id="default",
            ))
            await second_db.commit()
            # In-progress business changes stay private until the turn succeeds.
            first = await second_db.get(ConversationSession, "first")
            assert first is not None
            assert first.workflow_state is None
        finally:
            release_model.set()
            await task


async def test_real_api_key_auth_does_not_hold_writer_lock_during_model_wait(runtime_sessions, monkeypatch):
    from starlette.requests import Request
    from server.config import settings
    from server.middleware.auth import get_current_user, hash_api_key
    from server.models.user import APIKey, User

    monkeypatch.setattr(settings, "disable_auth", False)
    async with runtime_sessions() as db:
        db.add(User(id="owner", username="owner", password_hash="unused", tenant_id="default", role="editor"))
        db.add(APIKey(id="key", user_id="owner", tenant_id="default", key_hash=hash_api_key("offline-key"), scopes=["invoke"]))
        db.add(ConversationSession(id="existing", agent_id="agent", user_id="anonymous", tenant_id="default"))
        await db.commit()

    started, release = asyncio.Event(), asyncio.Event()

    async def pipeline(self, agent, session, req, trace_id, audit, event_cb=None):
        started.set()
        await release.wait()
        return InvokeResponse(session_id=session.id, trace_id=trace_id, short_answer="ok")

    monkeypatch.setattr(AgentRuntime, "_invoke_conversational", pipeline)
    async with runtime_sessions() as first, runtime_sessions() as second:
        request = Request({"type": "http", "headers": [(b"x-api-key", b"offline-key")]})
        identity = await get_current_user(request, None, first)
        assert identity["api_key_id"] == "key"
        assert not first.dirty
        task = asyncio.create_task(AgentRuntime(first).invoke(InvokeRequest(agent_id="agent", session_id="existing", message="hello")))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            second.add(ConversationSession(id="parallel", agent_id="agent", user_id="anonymous", tenant_id="default"))
            await second.commit()
            assert (await second.get(APIKey, "key")).last_used_at is not None
        finally:
            release.set()
            await task


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_turn_rolls_back_before_independent_audit_flush(
    runtime_sessions, monkeypatch, cancel,
):
    async with runtime_sessions() as db:
        db.add(ConversationSession(
            id="session", agent_id="agent", user_id="anonymous", tenant_id="default",
            workflow_state={"status": "waiting_input"},
        ))
        await db.commit()

    write_started = asyncio.Event()

    async def failing_pipeline(self, agent, session, req, trace_id, audit, event_cb=None):
        session.workflow_state = {"status": "completed"}
        await self._save_message(session.id, "user", req.message, trace_id)
        write_started.set()
        if cancel:
            await asyncio.Event().wait()
        raise RuntimeError("provider failed")

    monkeypatch.setattr(AgentRuntime, "_invoke_conversational", failing_pipeline)
    async with runtime_sessions() as db:
        task = asyncio.create_task(AgentRuntime(db).invoke(InvokeRequest(
            agent_id="agent", session_id="session", message="submit",
        )))
        await asyncio.wait_for(write_started.wait(), timeout=2)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
        # The SSE dependency can exit normally after sending an error event.
        # Its final commit must not publish a failed turn's partial writes.
        await db.commit()

    async with runtime_sessions() as db:
        session = await db.get(ConversationSession, "session")
        assert session.workflow_state == {"status": "waiting_input"}
        assert not (await db.scalars(select(Message))).all()
        traces = (await db.scalars(select(AuditTrace))).all()
        assert [trace.event_type for trace in traces] == ["user_input"]


async def test_same_session_turns_remain_serialized(runtime_sessions, monkeypatch):
    from server.api.invoke import invoke

    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()

    async def pipeline(self, agent, session, req, trace_id, audit, event_cb=None):
        if req.message == "first":
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
            assert session.message_count == 1
        await self._save_message(session.id, "user", req.message, trace_id)
        await self._save_session(session)
        return InvokeResponse(session_id=session.id, trace_id=trace_id, short_answer="ok")

    monkeypatch.setattr(AgentRuntime, "_invoke_conversational", pipeline)

    async def call(message):
        async with runtime_sessions() as db:
            return await invoke(
                InvokeRequest(agent_id="agent", session_id="shared", message=message),
                db=db, tenant_id="default", idempotency_key=None,
            )

    first = asyncio.create_task(call("first"))
    await asyncio.wait_for(first_started.wait(), timeout=2)
    second = asyncio.create_task(call("second"))
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(second_started.wait(), timeout=0.05)
    finally:
        release_first.set()
        await asyncio.gather(first, second)


@pytest.mark.parametrize("child_succeeds", [False, True])
async def test_delegation_commit_and_rollback_do_not_change_parent_transaction(
    runtime_sessions, monkeypatch, child_succeeds,
):
    async with runtime_sessions() as db:
        db.add(Agent(id="child", name="Child agent", tenant_id="default"))
        db.add(ConversationSession(
            id="parent", agent_id="agent", user_id="anonymous", tenant_id="default",
            workflow_state={"status": "waiting_input"},
        ))
        await db.commit()

    async def pipeline(self, agent, session, req, trace_id, audit, event_cb=None):
        if agent.id == "agent":
            session.workflow_state = {"status": "in_progress"}
            handler = self._make_delegate_handler(
                Skill(id="delegate", name="Delegate", skill_type="delegate"),
                {"target_agent_id": "child"}, agent, session, audit,
            )
            await handler({"message": "delegated task"})
            if child_succeeds:
                raise RuntimeError("parent failed after child committed")
        else:
            await self._save_message(session.id, "user", req.message, trace_id)
            if not child_succeeds:
                raise RuntimeError("child failed")
        await self._save_session(session)
        return InvokeResponse(session_id=session.id, trace_id=trace_id, short_answer="ok")

    monkeypatch.setattr(AgentRuntime, "_invoke_conversational", pipeline)
    async with runtime_sessions() as db:
        request = InvokeRequest(agent_id="agent", session_id="parent", message="delegate")
        if child_succeeds:
            with pytest.raises(RuntimeError, match="parent failed"):
                await AgentRuntime(db).invoke(request)
        else:
            await AgentRuntime(db).invoke(request)

    async with runtime_sessions() as db:
        parent = await db.get(ConversationSession, "parent")
        expected_status = "waiting_input" if child_succeeds else "in_progress"
        assert parent.workflow_state == {"status": expected_status}
        child = await db.scalar(select(ConversationSession).where(
            ConversationSession.agent_id == "child",
        ))
        assert child.parent_session_id == "parent"
        assert child.message_count == (1 if child_succeeds else 0)
        messages = (await db.scalars(select(Message))).all()
        assert len(messages) == (1 if child_succeeds else 0)


@pytest.mark.parametrize("status", ["waiting_input", "completed", "escalated"])
async def test_workflow_response_is_saved_for_exact_session_replay(runtime_sessions, status):
    card = WorkflowCard(
        step_name="Confirm order", step_type="confirm", prompt="Confirm order 42",
        collected_data={"order_id": 42}, current_step=1, total_steps=3,
    )
    async with runtime_sessions() as db:
        session = ConversationSession(
            id="replay", agent_id="agent", user_id="anonymous", tenant_id="default",
            workflow_state={"status": "in_progress"},
        )
        db.add(session)
        await db.commit()
        runtime = AgentRuntime(db)
        audit = AuditLogger(db, "trace", session.id, "agent")
        response = await runtime._finalize_workflow_turn(
            session, InvokeRequest(agent_id="agent", message="confirm"), "trace", audit,
            WorkflowStepResult(status=status, message="Order 42", card=card, escalated=status == "escalated"),
        )

    async with runtime_sessions() as db:
        saved = await db.scalar(select(Message).where(Message.role == "assistant"))
        assert saved.metadata_["workflow_card"] == response.workflow_card.model_dump(mode="json")
        assert saved.metadata_["workflow_status"] == response.workflow_status
        assert saved.metadata_["escalated"] == response.escalated
        assert saved.metadata_["escalation_reason"] == response.escalation_reason
        assert saved.suggested_followups == response.suggested_followups
        assert saved.short_answer == response.short_answer
        assert saved.trace_id == response.trace_id


async def test_message_saves_response_columns_and_metadata(runtime_sessions):
    response = InvokeResponse(
        session_id="replay", trace_id="trace", short_answer="Order shipped",
        expanded_answer="Tracking: 42", citations=[Citation(
            source_id="orders", source_name="Shipping policy", content_snippet="Next day",
        )], suggested_followups=["Track package"],
        metadata={"tool_calls": [{"name": "lookup_order"}]},
        skill_info={"skill_id": "order-tool", "skill_type": "http_tool"},
    )
    async with runtime_sessions() as db:
        await AgentRuntime(db)._save_message(
            response.session_id, "assistant", response.short_answer, response.trace_id,
            response=response,
        )
        await db.commit()
    async with runtime_sessions() as db:
        saved = await db.scalar(select(Message))
        assert saved.expanded_answer == response.expanded_answer
        assert saved.citations == [citation.model_dump(mode="json") for citation in response.citations]
        assert saved.suggested_followups == response.suggested_followups
        assert saved.metadata_["metadata"] == response.metadata
        assert saved.metadata_["skill_info"] == response.skill_info
