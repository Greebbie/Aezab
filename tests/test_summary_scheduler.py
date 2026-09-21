"""Tests for server/engine/summary_scheduler.py (Wave 5 / Workstream B).

Covers:
  - `schedule_summary_update`: fire-and-forget, non-raising, dedupes
    concurrent schedules for the same session_id via the in-flight set
    (mirrors server/engine/event_dispatcher.py's emit_event shape).
  - `_fold_session_summary` (the task body): re-applies the exact
    threshold/window gate the old inline `AgentRuntime._maybe_update_summary`
    used to apply, folds onto any prior summary, advances the
    `summarized_upto` watermark, persists to `session.context`, and never
    raises on LLM failure (leaves context untouched).
  - Lock scope: the (expensive) LLM fold call runs with NO session lock
    held — only the final persist takes `session_lock`, so a concurrent
    /invoke for the same session never blocks behind a fold's LLM
    round-trip. The phase-3 watermark re-check discards a stale fold
    result if another fold covered the same rows in the meantime.

`_fold_session_summary` opens its OWN `async_session` (per the module
docstring — never the caller's), so these tests point the module's
`async_session` at an isolated in-memory SQLite engine for the duration of
each test (mirroring tests/test_event_dispatcher.py's `db_session_maker`
fixture) rather than touching the real project database file.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import StaticPool

from server.db import Base
from server.engine import request_guard
from server.engine import summary_scheduler as ss
from server.engine.llm_adapter import LLMResponse
from server.models.agent import Agent
from server.models.session import ConversationSession, Message
from server.engine.context_budget import estimate_input_tokens


@pytest_asyncio.fixture
async def db_session_maker(monkeypatch):
    """Isolated in-memory engine; summary_scheduler.async_session is patched
    to use it so _fold_session_summary's own session sees exactly what the
    test seeds, without touching the real project database file."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ss, "async_session", session_maker)

    yield session_maker
    await engine.dispose()


def _make_agent() -> Agent:
    return Agent(
        id=str(uuid.uuid4()),
        name=f"agent-{uuid.uuid4().hex[:6]}",
        tenant_id="default",
        enabled=True,
    )


async def _async_return(value):
    return value


async def _seed(
    session_maker, message_count: int, total_messages: int, context: dict | None = None,
) -> tuple[str, str]:
    """Seed an agent + session + `total_messages` Message rows via the test
    engine. Returns (session_id, agent_id)."""
    agent = _make_agent()
    session_id = str(uuid.uuid4())
    async with session_maker() as db:
        db.add(agent)
        session = ConversationSession(
            id=session_id, agent_id=agent.id, user_id="u1",
            tenant_id=agent.tenant_id, message_count=message_count,
            context=context,
        )
        db.add(session)
        await db.flush()

        base = datetime.utcnow()
        for i in range(total_messages):
            db.add(Message(
                id=str(uuid.uuid4()),
                session_id=session_id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"msg-{i}",
                created_at=base + timedelta(seconds=i),
            ))
        await db.commit()
    return session_id, agent.id


async def _reload_context(session_maker, session_id: str) -> dict | None:
    async with session_maker() as db:
        session = await db.get(ConversationSession, session_id)
        return session.context


def test_summary_request_bounds_oversized_json_escaped_message():
    row = Message(role="user", content=('"\\\n订单ABC123' * 10000))
    prompt = ss._summary_prompt("先前摘要" * 10000, [row])
    assert estimate_input_tokens(prompt) <= ss.SUMMARY_INPUT_TOKENS
    assert "untrusted conversation" in prompt[0].content
    assert "内容已截断" in prompt[1].content


async def test_backlog_advances_only_the_bounded_batch_and_preserves_context(db_session_maker, monkeypatch):
    total = ss.SUMMARY_BATCH_ROWS * 2 + ss.RECENT_WINDOW
    session_id, agent_id = await _seed(
        db_session_maker, message_count=total // 2, total_messages=total,
        context={"business_reference": "ORD-123"},
    )
    batches = []

    async def summarize(db, agent, prior_summary, messages):
        batches.append([m.content for m in messages])
        assert estimate_input_tokens(ss._summary_prompt(prior_summary, messages)) <= ss.SUMMARY_INPUT_TOKENS
        return "order ORD-123 is waiting for confirmation"

    monkeypatch.setattr(ss, "_summarize_messages_llm", summarize)
    await ss._fold_session_summary(session_id, agent_id)
    context = await _reload_context(db_session_maker, session_id)
    assert context["summarized_upto"] == len(batches[0]) == ss.SUMMARY_BATCH_ROWS
    assert context["business_reference"] == "ORD-123"
    await ss._fold_session_summary(session_id, agent_id)
    context = await _reload_context(db_session_maker, session_id)
    assert context["summarized_upto"] == total - ss.RECENT_WINDOW
    assert batches[1][0] == f"msg-{ss.SUMMARY_BATCH_ROWS}"


# ── schedule_summary_update: fire-and-forget + dedupe ──────────────


@pytest.mark.asyncio
async def test_schedule_summary_update_dedupes_while_in_flight(monkeypatch):
    calls: list[tuple[str, str]] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_fold(session_id, agent_id):
        calls.append((session_id, agent_id))
        started.set()
        await release.wait()

    monkeypatch.setattr(ss, "_fold_session_summary", fake_fold)

    ss.schedule_summary_update("sess-dedupe", "agent-x")
    await asyncio.wait_for(started.wait(), timeout=1)

    # A second schedule for the SAME session while the first is in flight
    # must be a no-op — the fold is idempotent via the watermark, so the
    # next turn will simply catch up.
    ss.schedule_summary_update("sess-dedupe", "agent-x")
    await asyncio.sleep(0.02)
    assert calls == [("sess-dedupe", "agent-x")]

    release.set()
    for _ in range(100):
        if "sess-dedupe" not in ss._inflight_sessions:
            break
        await asyncio.sleep(0.01)
    assert "sess-dedupe" not in ss._inflight_sessions


@pytest.mark.asyncio
async def test_schedule_summary_update_allows_new_schedule_after_completion(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_fold(session_id, agent_id):
        calls.append((session_id, agent_id))

    monkeypatch.setattr(ss, "_fold_session_summary", fake_fold)

    ss.schedule_summary_update("sess-reuse", "agent-x")
    for _ in range(100):
        if "sess-reuse" not in ss._inflight_sessions:
            break
        await asyncio.sleep(0.01)

    ss.schedule_summary_update("sess-reuse", "agent-x")
    for _ in range(100):
        if "sess-reuse" not in ss._inflight_sessions:
            break
        await asyncio.sleep(0.01)

    assert calls == [("sess-reuse", "agent-x"), ("sess-reuse", "agent-x")]


@pytest.mark.asyncio
async def test_schedule_summary_update_is_non_raising_on_fold_failure(monkeypatch):
    async def failing_fold(session_id, agent_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(ss, "_fold_session_summary", failing_fold)

    # Must not raise synchronously.
    ss.schedule_summary_update("sess-fail", "agent-x")
    for _ in range(100):
        if "sess-fail" not in ss._inflight_sessions:
            break
        await asyncio.sleep(0.01)
    # In-flight marker cleared even though the fold raised.
    assert "sess-fail" not in ss._inflight_sessions


# ── _fold_session_summary: gate respected ───────────────────────────


@pytest.mark.asyncio
async def test_fold_skips_below_row_threshold(monkeypatch, db_session_maker):
    class _NeverCalledLLM:
        async def chat(self, messages, **kwargs):
            raise AssertionError("LLM must not be called below threshold")

    monkeypatch.setattr(ss, "get_llm_adapter_for_agent", lambda agent, db: _async_return(_NeverCalledLLM()))

    session_id, agent_id = await _seed(
        db_session_maker, ss.SUMMARY_THRESHOLD, ss.SUMMARY_THRESHOLD - 1,
    )

    await ss._fold_session_summary(session_id, agent_id)

    assert await _reload_context(db_session_maker, session_id) in (None, {})


@pytest.mark.asyncio
async def test_fold_skips_when_not_enough_old_messages(monkeypatch, db_session_maker):
    class _NeverCalledLLM:
        async def chat(self, messages, **kwargs):
            raise AssertionError("LLM must not be called with no old messages")

    monkeypatch.setattr(ss, "get_llm_adapter_for_agent", lambda agent, db: _async_return(_NeverCalledLLM()))
    monkeypatch.setattr(ss, "SUMMARY_THRESHOLD", 4)
    monkeypatch.setattr(ss, "RECENT_WINDOW", 6)

    # == patched RECENT_WINDOW, nothing older to fold
    session_id, agent_id = await _seed(db_session_maker, 4, 6)

    await ss._fold_session_summary(session_id, agent_id)

    assert await _reload_context(db_session_maker, session_id) in (None, {})


@pytest.mark.asyncio
async def test_fold_triggers_past_threshold_and_persists(monkeypatch, db_session_maker):
    class _FakeLLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            return LLMResponse(content="用户咨询了退款政策并已确认流程。", tool_calls=None)

    llm = _FakeLLM()
    monkeypatch.setattr(ss, "get_llm_adapter_for_agent", lambda agent, db: _async_return(llm))

    total = ss.SUMMARY_THRESHOLD + 3
    session_id, agent_id = await _seed(db_session_maker, ss.SUMMARY_THRESHOLD, total)

    await ss._fold_session_summary(session_id, agent_id)

    assert llm.calls == 1
    context = await _reload_context(db_session_maker, session_id)
    assert context == {
        "summary": "用户咨询了退款政策并已确认流程。",
        "summarized_upto": total - ss.RECENT_WINDOW,
    }


@pytest.mark.asyncio
async def test_fold_folds_onto_prior_summary_and_advances_watermark(monkeypatch, db_session_maker):
    class _FakeLLM:
        def __init__(self):
            self.calls = 0
            self.last_user_prompt = None

        async def chat(self, messages, **kwargs):
            self.calls += 1
            self.last_user_prompt = messages[-1].content
            return LLMResponse(content="folded summary v2", tool_calls=None)

    llm = _FakeLLM()
    monkeypatch.setattr(ss, "get_llm_adapter_for_agent", lambda agent, db: _async_return(llm))

    total = ss.RECENT_WINDOW + 6  # boundary = total - window = 6 > summarized_upto(3)
    session_id, agent_id = await _seed(
        db_session_maker, ss.SUMMARY_THRESHOLD, total,
        context={"summary": "old summary", "summarized_upto": 3},
    )

    await ss._fold_session_summary(session_id, agent_id)

    assert llm.calls == 1
    assert "old summary" in llm.last_user_prompt
    context = await _reload_context(db_session_maker, session_id)
    assert context == {"summary": "folded summary v2", "summarized_upto": 6}


@pytest.mark.asyncio
async def test_fold_already_folded_up_to_boundary_skips_llm(monkeypatch, db_session_maker):
    class _NeverCalledLLM:
        async def chat(self, messages, **kwargs):
            raise AssertionError("LLM must not be called with nothing new to fold")

    monkeypatch.setattr(ss, "get_llm_adapter_for_agent", lambda agent, db: _async_return(_NeverCalledLLM()))

    total = ss.SUMMARY_THRESHOLD + 3
    boundary = total - ss.RECENT_WINDOW
    session_id, agent_id = await _seed(
        db_session_maker, ss.SUMMARY_THRESHOLD, total,
        context={"summary": "already up to date", "summarized_upto": boundary},
    )

    await ss._fold_session_summary(session_id, agent_id)

    context = await _reload_context(db_session_maker, session_id)
    assert context == {"summary": "already up to date", "summarized_upto": boundary}


@pytest.mark.asyncio
async def test_fold_llm_failure_is_logged_and_context_unchanged(monkeypatch, db_session_maker, caplog):
    class _FailingLLM:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("llm down")

    monkeypatch.setattr(ss, "get_llm_adapter_for_agent", lambda agent, db: _async_return(_FailingLLM()))

    total = ss.SUMMARY_THRESHOLD + 3
    session_id, agent_id = await _seed(
        db_session_maker, ss.SUMMARY_THRESHOLD, total,
        context={"summary": "prior summary", "summarized_upto": 0},
    )

    # schedule_summary_update must never raise even though the fold's LLM
    # call fails deep inside it.
    ss.schedule_summary_update(session_id, agent_id)
    for _ in range(100):
        if session_id not in ss._inflight_sessions:
            break
        await asyncio.sleep(0.01)

    assert session_id not in ss._inflight_sessions
    context = await _reload_context(db_session_maker, session_id)
    assert context == {"summary": "prior summary", "summarized_upto": 0}
    assert "fold failed" in caplog.text


@pytest.mark.asyncio
async def test_fold_missing_session_is_a_noop(db_session_maker):
    # Neither session nor agent exist — must return quietly, not raise.
    await ss._fold_session_summary(str(uuid.uuid4()), str(uuid.uuid4()))


# ── Lock scope: LLM call lock-free, persist under session_lock ──────


@pytest.mark.asyncio
async def test_fold_llm_call_runs_lock_free_and_persist_takes_session_lock(
    monkeypatch, db_session_maker,
):
    """The whole point of the async fold is to keep the LLM round-trip off
    every /invoke's critical path — including the NEXT invoke for the same
    session, which takes session_lock in server/api/invoke.py. So the fold's
    LLM call must run with NO session lock held (a concurrent invoke would
    otherwise block behind it for the full LLM latency), while the final
    persist must still serialize via session_lock."""
    lock_count_during_llm: list[int] = []

    class _RecordingLLM:
        async def chat(self, messages, **kwargs):
            lock_count_during_llm.append(request_guard.active_session_lock_count())
            return LLMResponse(content="folded lock-free", tool_calls=None)

    monkeypatch.setattr(
        ss, "get_llm_adapter_for_agent",
        lambda agent, db: _async_return(_RecordingLLM()),
    )

    # Wrap the real session_lock so we can assert the persist phase actually
    # acquired it (and for the right session_id).
    locked_session_ids: list[str] = []
    real_session_lock = request_guard.session_lock

    @contextlib.asynccontextmanager
    async def recording_session_lock(sid):
        async with real_session_lock(sid):
            locked_session_ids.append(sid)
            yield

    monkeypatch.setattr(ss, "session_lock", recording_session_lock)

    total = ss.SUMMARY_THRESHOLD + 3
    session_id, agent_id = await _seed(db_session_maker, ss.SUMMARY_THRESHOLD, total)

    assert request_guard.active_session_lock_count() == 0
    await ss._fold_session_summary(session_id, agent_id)

    # LLM call saw zero held session locks (phase 2 is lock-free).
    assert lock_count_during_llm == [0]
    # Persist phase took session_lock(session_id) exactly once.
    assert locked_session_ids == [session_id]
    # And the fold actually persisted.
    context = await _reload_context(db_session_maker, session_id)
    assert context == {
        "summary": "folded lock-free",
        "summarized_upto": total - ss.RECENT_WINDOW,
    }
    # Lock fully released afterwards.
    assert request_guard.active_session_lock_count() == 0


@pytest.mark.asyncio
async def test_fold_watermark_recheck_discards_stale_result(monkeypatch, db_session_maker):
    """If another fold advances the persisted watermark past this fold's
    boundary between phase 1 (gate/compute) and phase 3 (persist), the
    re-check under the lock must discard this fold's stale result instead
    of overwriting the newer summary."""
    total = ss.SUMMARY_THRESHOLD + 3
    boundary = total - ss.RECENT_WINDOW

    seeded: dict[str, str] = {}

    class _RacingLLM:
        async def chat(self, messages, **kwargs):
            # Simulate a concurrent fold completing while this one is in its
            # (lock-free) LLM call: advance the persisted watermark past
            # `boundary` before phase 3 runs.
            async with db_session_maker() as db:
                session = await db.get(ConversationSession, seeded["session_id"])
                session.context = {
                    "summary": "newer concurrent fold",
                    "summarized_upto": boundary + 2,
                }
                flag_modified(session, "context")
                await db.commit()
            return LLMResponse(content="stale fold result", tool_calls=None)

    monkeypatch.setattr(
        ss, "get_llm_adapter_for_agent",
        lambda agent, db: _async_return(_RacingLLM()),
    )

    session_id, agent_id = await _seed(db_session_maker, ss.SUMMARY_THRESHOLD, total)
    seeded["session_id"] = session_id

    await ss._fold_session_summary(session_id, agent_id)

    # The stale result was discarded; the concurrent fold's context stands.
    context = await _reload_context(db_session_maker, session_id)
    assert context == {"summary": "newer concurrent fold", "summarized_upto": boundary + 2}
