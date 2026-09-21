"""Tests for server/engine/context_budget.py"""
import pytest

from server.engine.context_budget import (
    estimate_tokens, truncate_text, trim_messages,
    ContextBudgetExceeded, estimate_input_tokens,
)
from server.engine.llm_adapter import LLMMessage


def test_estimate_tokens_cjk_heavier_than_ascii():
    # CJK ~1 token/char, ASCII ~1 token per 4 chars
    assert estimate_tokens("你好世界") >= 4
    assert estimate_tokens("abcd") <= 2
    assert estimate_tokens("") == 0


def test_truncate_text_under_limit_unchanged():
    assert truncate_text("short", 100) == "short"


def test_truncate_text_over_limit_truncated_with_marker():
    long_text = "长" * 5000
    out = truncate_text(long_text, 100)
    assert len(out) < len(long_text)
    assert out.endswith("…[内容已截断]")
    assert estimate_tokens(out) <= 100


def test_trim_messages_keeps_system_and_last_user():
    msgs = [LLMMessage(role="system", content="sys")]
    for i in range(50):
        msgs.append(LLMMessage(role="user", content="问" * 2000))
        msgs.append(LLMMessage(role="assistant", content="答" * 2000))
    msgs.append(LLMMessage(role="user", content="最新问题"))
    out = trim_messages(msgs, max_input_tokens=8000)
    assert out[0].role == "system"
    assert out[-1].content == "最新问题"
    total = sum(estimate_tokens(m.content or "") for m in out)
    assert total <= 8000


def test_trim_messages_drops_tool_call_groups_atomically():
    # An assistant tool_calls message must never survive without its tool replies
    msgs = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="old q " * 3000),
        LLMMessage(role="assistant", content="", tool_calls=[{"id": "t1"}]),
        LLMMessage(role="tool", content="result " * 3000, tool_call_id="t1"),
        LLMMessage(role="user", content="new q"),
    ]
    out = trim_messages(msgs, max_input_tokens=500)
    roles = [m.role for m in out]
    # either both assistant(tool_calls)+tool survive or neither does
    has_assistant_tc = any(m.role == "assistant" and m.tool_calls for m in out)
    has_tool = any(m.role == "tool" for m in out)
    assert has_assistant_tc == has_tool
    assert roles[0] == "system" and out[-1].content == "new q"


def test_trim_messages_no_budget_returns_same():
    msgs = [LLMMessage(role="system", content="s"), LLMMessage(role="user", content="u")]
    assert trim_messages(msgs, max_input_tokens=None) == msgs


def test_trim_messages_trailing_tool_group_stays_atomic():
    """Regression: when the message list ends with tool replies (the shape at
    the top of every tool-loop round), the trailing assistant(tool_calls) +
    tool messages must survive trimming together — an orphaned tool message
    is rejected by OpenAI-compatible APIs."""
    msgs = [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="旧问题 " * 3000),
        LLMMessage(role="user", content="current request"),
        LLMMessage(role="assistant", content="", tool_calls=[{"id": "t1"}, {"id": "t2"}]),
        LLMMessage(role="tool", content="result1 " * 3000, tool_call_id="t1"),
        LLMMessage(role="tool", content="result2 " * 3000, tool_call_id="t2"),
    ]
    out = trim_messages(msgs, max_input_tokens=100)
    assert estimate_input_tokens(out) <= 100
    assert any(m.content == "current request" for m in out)
    has_assistant_tc = any(m.role == "assistant" and m.tool_calls for m in out)
    has_tool = any(m.role == "tool" for m in out)
    assert has_assistant_tc == has_tool
    # every tool message must be preceded (eventually) by its assistant group head
    for i, m in enumerate(out):
        if m.role == "tool":
            prev_roles = [p.role for p in out[:i]]
            assert "assistant" in prev_roles


def test_large_required_instructions_or_request_fail_before_provider_call():
    for messages in [
        [LLMMessage("system", "长" * 1000), LLMMessage("user", "hello")],
        [LLMMessage("system", "sys"), LLMMessage("user", "长" * 1000)],
    ]:
        with pytest.raises(ContextBudgetExceeded):
            trim_messages(messages, 100)


def test_tool_schemas_and_arguments_are_charged_without_rewriting_json():
    messages = [LLMMessage("system", "sys"), LLMMessage("user", "hello")]
    with pytest.raises(ContextBudgetExceeded):
        trim_messages(messages, 100, tools=[{"description": "长" * 1000}])
    messages.extend([
        LLMMessage("assistant", "", tool_calls=[{"id": "t1", "arguments": "长" * 1000}]),
        LLMMessage("tool", "result", tool_call_id="t1"),
    ])
    with pytest.raises(ContextBudgetExceeded):
        trim_messages(messages, 100)
    assert messages[-1].content == "result"


def test_small_truncation_budgets_never_overflow_on_marker():
    for budget in range(12):
        assert estimate_tokens(truncate_text("长" * 100, budget)) <= budget


def test_summary_survives_before_recent_small_talk_when_budget_is_tight():
    messages = [
        LLMMessage("system", "instructions"),
        LLMMessage("system", 'Historical data: {"order_id":"ABC123"}'),
        LLMMessage("user", "small talk " * 20),
        LLMMessage("assistant", "small talk " * 20),
        LLMMessage("user", "查询这个订单"),
    ]
    kept = trim_messages(messages, 70)
    assert kept[:2] == messages[:2]
    assert kept[-1] == messages[-1]
    assert estimate_input_tokens(kept) <= 70
