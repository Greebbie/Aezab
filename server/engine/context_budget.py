"""Token budget guardrails for the conversational pipeline.

Approximate token counting (no tiktoken dependency): CJK chars count ~1
token each, other chars ~0.25. Good enough for guardrails, not billing.
"""
from __future__ import annotations

import os

MAX_TOOL_RESULT_TOKENS = int(os.getenv("HLAB_MAX_TOOL_RESULT_TOKENS", "2000"))
MAX_INPUT_TOKENS = int(os.getenv("HLAB_MAX_INPUT_TOKENS", "24000"))

_TRUNCATE_MARKER = "\n…[内容已截断]"


def estimate_tokens(text: str) -> int:
    """Rough token estimate: CJK chars ~1 token, other chars ~0.25 token."""
    if not text:
        return 0
    cjk = sum(1 for c in text if ord(c) > 0x2E80)
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def truncate_text(text: str, max_tokens: int, marker: str = _TRUNCATE_MARKER) -> str:
    """Truncate text to fit within max_tokens (estimated), appending a marker."""
    if estimate_tokens(text) <= max_tokens:
        return text
    # binary-search-free approximation: cut proportionally, then trim to fit
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + marker


def trim_messages(messages: list, max_input_tokens: int | None = None) -> list:
    """Return a new message list fitting the budget.

    Always keeps the system message (index 0 if role==system) and the final
    message. Drops oldest non-system messages first. An assistant message
    carrying tool_calls and its following role=="tool" replies are treated
    as one atomic group (OpenAI API rejects orphaned tool messages).
    """
    if max_input_tokens is None:
        return messages
    if not messages:
        return messages

    def cost(m) -> int:
        return estimate_tokens(getattr(m, "content", "") or "") + 4

    total = sum(cost(m) for m in messages)
    if total <= max_input_tokens:
        return messages

    head = []
    body = list(messages)
    if body and body[0].role == "system":
        head = [body.pop(0)]

    # group body into atomic units (tool-call groups stay together)
    groups: list[list] = []
    i = 0
    while i < len(body):
        m = body[i]
        if m.role == "assistant" and getattr(m, "tool_calls", None):
            group = [m]
            i += 1
            while i < len(body) and body[i].role == "tool":
                group.append(body[i])
                i += 1
            groups.append(group)
        else:
            groups.append([m])
            i += 1

    # The newest group is kept unconditionally. Using the group (not the last
    # single message) as the fixed tail keeps a trailing tool reply attached
    # to the assistant tool_calls message that requested it.
    tail = groups.pop() if groups else []

    fixed = sum(cost(m) for m in head + tail)
    kept: list[list] = []
    budget = max_input_tokens - fixed
    # keep newest groups first
    for group in reversed(groups):
        g_cost = sum(cost(m) for m in group)
        if g_cost <= budget:
            kept.append(group)
            budget -= g_cost
    kept.reverse()
    return head + [m for g in kept for m in g] + tail
