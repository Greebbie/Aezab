"""Tests for Wave 4 / Workstream M current-session memory, updated for
Wave 5 / Workstream B's async rolling summary (server/engine/summary_scheduler.py).

Covers:
  - `_get_persisted_summary`: a cheap READ of whatever a prior turn's
    background fold already persisted onto `session.context["summary"]` —
    no DB scan, no LLM call.
  - Full-pipeline scheduling: a past-threshold turn schedules a background
    fold via `schedule_summary_update` (mocked here — the fold's own gate
    and persistence logic is covered by tests/test_summary_scheduler.py)
    instead of making an inline LLM call for summarization; a
    below-threshold turn does not schedule anything.
  - Message assembly: the `[对话摘要]` line is injected ahead of the recent
    window when a summary is already persisted on `session.context`;
    omitted when it is not (today's raw window behavior, now with a
    configurable window size).
  - Query rewrite (`_llm_rewrite_query`): uses a one-shot LLM call for
    coreference resolution, falls back to the string-concat heuristic on
    failure or an empty LLM response, and `_needs_query_rewrite` still
    gates most turns out.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from server.db import async_session
from server.engine.agent_runtime import (
    RECENT_WINDOW,
    SUMMARY_THRESHOLD,
    AgentRuntime,
    SkillToolResult,
    _needs_query_rewrite,
)
from server.engine.audit_logger import AuditLogger, new_trace_id
from server.engine.llm_adapter import LLMResponse
from server.models.agent import Agent
from server.models.session import ConversationSession, Message
from server.schemas.invoke import InvokeRequest


def _make_agent() -> Agent:
    return Agent(
        id=str(uuid.uuid4()),
        name=f"agent-{uuid.uuid4().hex[:6]}",
        tenant_id="default",
        enabled=True,
    )


async def _async_return(value):
    return value


async def _seed_messages(db, session_id: str, count: int, role_pattern=("user", "assistant")):
    base = datetime.utcnow()
    for i in range(count):
        db.add(Message(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role=role_pattern[i % len(role_pattern)],
            content=f"msg-{i}",
            created_at=base + timedelta(seconds=i),
        ))
    await db.flush()


async def _fake_build_skill_tools(self, agent, session, audit, pre_retrieved=False, preloaded_skills=None):
    """Bypass real DB-backed skill registration for full-pipeline tests."""
    tool_defs = [{
        "type": "function",
        "function": {
            "name": "noop_tool",
            "description": "does nothing",
            "parameters": {"type": "object", "properties": {}},
        },
    }]

    async def _handler(args: dict) -> SkillToolResult:
        return SkillToolResult(text="tool result")

    return tool_defs, {"noop_tool": _handler}


# ── _get_persisted_summary: cheap read, no DB scan, no LLM call ────


@pytest.mark.asyncio
async def test_get_persisted_summary_returns_none_without_context():
    async with async_session() as db:
        agent = _make_agent()
        db.add(agent)
        await db.flush()

        session = ConversationSession(
            id=str(uuid.uuid4()), agent_id=agent.id, user_id="u1",
            tenant_id=agent.tenant_id,
        )
        db.add(session)
        await db.flush()

        audit = AuditLogger(db, new_trace_id(), session.id, agent.id, agent.tenant_id)
        runtime = AgentRuntime(db)
        result = await runtime._get_persisted_summary(agent, session, audit)

        await db.rollback()

    assert result is None


@pytest.mark.asyncio
async def test_get_persisted_summary_returns_existing_summary_with_no_db_scan(monkeypatch):
    """Regression: this must be a pure dict read — no `_get_all_history`
    query and no LLM call, regardless of message_count/threshold."""
    async def _boom(self, session_id):
        raise AssertionError("must not query history — this is a cheap read now")

    class _NeverCalledLLM:
        async def chat(self, messages, **kwargs):
            raise AssertionError("must not call the LLM — this is a cheap read now")

    monkeypatch.setattr(
        "server.engine.agent_runtime.get_llm_adapter_for_agent",
        lambda agent, db: _async_return(_NeverCalledLLM()),
    )

    async with async_session() as db:
        agent = _make_agent()
        db.add(agent)
        await db.flush()

        session = ConversationSession(
            id=str(uuid.uuid4()), agent_id=agent.id, user_id="u1",
            tenant_id=agent.tenant_id, message_count=SUMMARY_THRESHOLD,
            context={"summary": "already persisted by a prior turn's fold", "summarized_upto": 6},
        )
        db.add(session)
        await db.flush()
        # Even with plenty of rows past the threshold, no scan should happen.
        await _seed_messages(db, session.id, SUMMARY_THRESHOLD + 3)

        audit = AuditLogger(db, new_trace_id(), session.id, agent.id, agent.tenant_id)
        runtime = AgentRuntime(db)
        result = await runtime._get_persisted_summary(agent, session, audit)

        await db.rollback()

    assert result == "already persisted by a prior turn's fold"


# ── Full-pipeline scheduling ─────────────────────────────────────


@pytest.mark.asyncio
async def test_full_invoke_past_threshold_schedules_background_fold_not_inline_llm(monkeypatch):
    """A past-threshold turn must schedule the fold in the background
    (schedule_summary_update) and must NOT make an inline LLM call for
    summarization — only the main tool-calling round's `chat_with_tools`
    call is allowed."""
    class _MainLoopOnlyLLM:
        async def chat_with_tools(self, messages, tools, **kwargs):
            return LLMResponse(content="final answer", tool_calls=None)

        async def chat(self, messages, **kwargs):
            raise AssertionError("no inline summarization LLM call should happen")

    monkeypatch.setattr(AgentRuntime, "_build_skill_tools", _fake_build_skill_tools)
    llm = _MainLoopOnlyLLM()
    monkeypatch.setattr(
        "server.engine.agent_runtime.get_llm_adapter_for_agent",
        lambda agent, db: _async_return(llm),
    )

    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "server.engine.agent_runtime.schedule_summary_update",
        lambda session_id, agent_id: scheduled.append((session_id, agent_id)),
    )

    async with async_session() as db:
        agent = _make_agent()
        db.add(agent)
        await db.flush()

        session = ConversationSession(
            id=str(uuid.uuid4()), agent_id=agent.id, user_id="u1",
            tenant_id=agent.tenant_id, message_count=SUMMARY_THRESHOLD,
        )
        db.add(session)
        await db.flush()

        runtime = AgentRuntime(db)
        req = InvokeRequest(agent_id=agent.id, session_id=session.id, user_id="u1", message="继续处理我的问题")
        response = await runtime.invoke(req)

        await db.rollback()

    assert response.short_answer == "final answer"
    assert scheduled == [(session.id, agent.id)]


@pytest.mark.asyncio
async def test_full_invoke_below_threshold_does_not_schedule(monkeypatch):
    class _MainLoopOnlyLLM:
        async def chat_with_tools(self, messages, tools, **kwargs):
            return LLMResponse(content="final answer", tool_calls=None)

        async def chat(self, messages, **kwargs):
            raise AssertionError("no inline summarization LLM call should happen")

    monkeypatch.setattr(AgentRuntime, "_build_skill_tools", _fake_build_skill_tools)
    llm = _MainLoopOnlyLLM()
    monkeypatch.setattr(
        "server.engine.agent_runtime.get_llm_adapter_for_agent",
        lambda agent, db: _async_return(llm),
    )

    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "server.engine.agent_runtime.schedule_summary_update",
        lambda session_id, agent_id: scheduled.append((session_id, agent_id)),
    )

    async with async_session() as db:
        agent = _make_agent()
        db.add(agent)
        await db.flush()

        session = ConversationSession(
            id=str(uuid.uuid4()), agent_id=agent.id, user_id="u1",
            tenant_id=agent.tenant_id, message_count=0,
        )
        db.add(session)
        await db.flush()

        runtime = AgentRuntime(db)
        req = InvokeRequest(agent_id=agent.id, session_id=session.id, user_id="u1", message="今天天气怎么样")
        response = await runtime.invoke(req)

        await db.rollback()

    assert response.short_answer == "final answer"
    assert scheduled == []


# ── Message assembly ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_messages_include_summary_line_ahead_of_recent_window(monkeypatch):
    """A summary already persisted by a PRIOR turn's background fold (not
    this turn's inline call — there is none) is injected as `[对话摘要]`
    ahead of the recent window."""
    class _CapturingLLM:
        def __init__(self):
            self.captured_messages = None

        async def chat_with_tools(self, messages, tools, **kwargs):
            self.captured_messages = list(messages)
            return LLMResponse(content="answer", tool_calls=None)

        async def chat(self, messages, **kwargs):
            raise AssertionError("no inline summarization LLM call should happen")

    monkeypatch.setattr(AgentRuntime, "_build_skill_tools", _fake_build_skill_tools)
    llm = _CapturingLLM()
    monkeypatch.setattr(
        "server.engine.agent_runtime.get_llm_adapter_for_agent",
        lambda agent, db: _async_return(llm),
    )
    monkeypatch.setattr("server.engine.agent_runtime.schedule_summary_update", lambda *a, **kw: None)

    async with async_session() as db:
        agent = _make_agent()
        db.add(agent)
        await db.flush()

        session = ConversationSession(
            id=str(uuid.uuid4()), agent_id=agent.id, user_id="u1",
            tenant_id=agent.tenant_id, message_count=SUMMARY_THRESHOLD,
            context={"summary": "folded summary text", "summarized_upto": 3},
        )
        db.add(session)
        await db.flush()
        await _seed_messages(db, session.id, SUMMARY_THRESHOLD + 3)

        runtime = AgentRuntime(db)
        req = InvokeRequest(agent_id=agent.id, session_id=session.id, user_id="u1", message="请继续帮我处理")
        response = await runtime.invoke(req)

        await db.rollback()

    assert response.short_answer == "answer"
    contents = [m.content for m in llm.captured_messages]
    summary_idx = next(i for i, c in enumerate(contents) if "conversation_memory" in c)
    assert '"conversation_memory": "folded summary text"' in contents[summary_idx]
    assert "untrusted historical data" in contents[summary_idx]
    # Recent window messages (all say "msg-N") must come after the summary line.
    history_indices = [i for i, c in enumerate(contents) if c.startswith("msg-")]
    assert history_indices, "expected recent-window messages in the prompt"
    assert all(i > summary_idx for i in history_indices)
    # Include the unsummarized gap while the background fold is still behind.
    assert len(history_indices) == SUMMARY_THRESHOLD


@pytest.mark.asyncio
async def test_messages_have_no_summary_line_below_threshold(monkeypatch):
    # This test inspects the current prompt, not a later background fold.
    monkeypatch.setattr("server.engine.agent_runtime.schedule_summary_update", lambda *a, **kw: None)

    class _CapturingLLM:
        def __init__(self):
            self.captured_messages = None

        async def chat_with_tools(self, messages, tools, **kwargs):
            self.captured_messages = list(messages)
            return LLMResponse(content="answer", tool_calls=None)

        async def chat(self, messages, **kwargs):
            raise AssertionError("summary LLM must not be called below threshold")

    monkeypatch.setattr(AgentRuntime, "_build_skill_tools", _fake_build_skill_tools)
    llm = _CapturingLLM()
    monkeypatch.setattr(
        "server.engine.agent_runtime.get_llm_adapter_for_agent",
        lambda agent, db: _async_return(llm),
    )

    async with async_session() as db:
        agent = _make_agent()
        db.add(agent)
        await db.flush()

        session = ConversationSession(
            id=str(uuid.uuid4()), agent_id=agent.id, user_id="u1",
            tenant_id=agent.tenant_id, message_count=SUMMARY_THRESHOLD // 2 - 1,
        )
        db.add(session)
        await db.flush()
        await _seed_messages(db, session.id, RECENT_WINDOW + 3)

        runtime = AgentRuntime(db)
        req = InvokeRequest(agent_id=agent.id, session_id=session.id, user_id="u1", message="今天天气怎么样")
        response = await runtime.invoke(req)

        await db.rollback()

    assert response.short_answer == "answer"
    contents = [m.content for m in llm.captured_messages]
    assert not any(c.startswith("[对话摘要]") for c in contents)
    # No summary exists yet: keep all raw history until the token budget fills.
    history_indices = [i for i, c in enumerate(contents) if c.startswith("msg-")]
    assert len(history_indices) == RECENT_WINDOW + 3


# ── _llm_rewrite_query ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_rewrite_query_uses_llm_when_it_succeeds(monkeypatch):
    class _FakeLLM:
        async def chat(self, messages, **kwargs):
            return LLMResponse(content="第二个产品的应用场景是什么", tool_calls=None)

    monkeypatch.setattr(
        "server.engine.agent_runtime.get_llm_adapter_for_agent",
        lambda agent, db: _async_return(_FakeLLM()),
    )

    async with async_session() as db:
        agent = _make_agent()
        db.add(agent)
        await db.flush()

        session = ConversationSession(
            id=str(uuid.uuid4()), agent_id=agent.id, user_id="u1", tenant_id=agent.tenant_id,
        )
        db.add(session)
        await db.flush()

        audit = AuditLogger(db, new_trace_id(), session.id, agent.id, agent.tenant_id)
        runtime = AgentRuntime(db)
        history = [
            Message(session_id=session.id, role="user", content="产品A和产品B有什么区别"),
            Message(session_id=session.id, role="assistant", content="产品A侧重速度，产品B侧重精度"),
        ]
        result = await runtime._llm_rewrite_query(agent, "那第二个呢", history, audit)

        await db.rollback()

    assert result == "第二个产品的应用场景是什么"


@pytest.mark.asyncio
async def test_llm_rewrite_query_falls_back_to_string_concat_on_failure(monkeypatch):
    class _FailingLLM:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("llm down")

    monkeypatch.setattr(
        "server.engine.agent_runtime.get_llm_adapter_for_agent",
        lambda agent, db: _async_return(_FailingLLM()),
    )

    async with async_session() as db:
        agent = _make_agent()
        db.add(agent)
        await db.flush()

        session = ConversationSession(
            id=str(uuid.uuid4()), agent_id=agent.id, user_id="u1", tenant_id=agent.tenant_id,
        )
        db.add(session)
        await db.flush()

        audit = AuditLogger(db, new_trace_id(), session.id, agent.id, agent.tenant_id)
        runtime = AgentRuntime(db)
        history = [
            Message(session_id=session.id, role="user", content="产品A是什么"),
            Message(session_id=session.id, role="assistant", content="产品A是一款检测工具"),
        ]
        result = await runtime._llm_rewrite_query(agent, "它有哪些应用", history, audit)

        await db.rollback()

    # Fallback: string-concat heuristic (last user message + current message).
    assert result == "产品A是什么 它有哪些应用"


@pytest.mark.asyncio
async def test_llm_rewrite_query_falls_back_on_empty_llm_response(monkeypatch):
    class _EmptyLLM:
        async def chat(self, messages, **kwargs):
            return LLMResponse(content="   ", tool_calls=None)

    monkeypatch.setattr(
        "server.engine.agent_runtime.get_llm_adapter_for_agent",
        lambda agent, db: _async_return(_EmptyLLM()),
    )

    async with async_session() as db:
        agent = _make_agent()
        db.add(agent)
        await db.flush()

        session = ConversationSession(
            id=str(uuid.uuid4()), agent_id=agent.id, user_id="u1", tenant_id=agent.tenant_id,
        )
        db.add(session)
        await db.flush()

        audit = AuditLogger(db, new_trace_id(), session.id, agent.id, agent.tenant_id)
        runtime = AgentRuntime(db)
        history = [Message(session_id=session.id, role="user", content="产品A是什么")]
        result = await runtime._llm_rewrite_query(agent, "它多少钱", history, audit)

        await db.rollback()

    assert result == "产品A是什么 它多少钱"


def test_needs_query_rewrite_skips_ordinary_standalone_questions():
    """Gating unchanged: a normal, self-contained question does not trigger
    the (costlier) LLM rewrite path at all."""
    assert _needs_query_rewrite("请问产品A的价格是多少钱一件") is False
    assert _needs_query_rewrite("它多少钱") is True  # short + pronoun -> gated in


# ── Cross-session hook (M-T3) ─────────────────────────────────────
