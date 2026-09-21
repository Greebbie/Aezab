"""Token budget guardrails for the conversational pipeline.

Approximate token counting (no tiktoken dependency): CJK chars count ~1
token each, other chars ~0.25. Good enough for guardrails, not billing.
"""
from __future__ import annotations

import json
from dataclasses import replace

from server.config import env_str

# Env: AEZAB_MAX_TOOL_RESULT_TOKENS / AEZAB_MAX_INPUT_TOKENS (legacy HLAB_
# names accepted as fallback).
MAX_TOOL_RESULT_TOKENS = int(env_str("MAX_TOOL_RESULT_TOKENS", "2000"))
MAX_INPUT_TOKENS = int(env_str("MAX_INPUT_TOKENS", "24000"))

_TRUNCATE_MARKER = "\n…[内容已截断]"


class ContextBudgetExceeded(ValueError):
    """Required instructions, current request, and tool schemas cannot fit."""


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
    max_tokens = max(0, max_tokens)
    if estimate_tokens(marker) > max_tokens:
        marker = ""
    content_budget = max_tokens - estimate_tokens(marker)
    # binary-search-free approximation: cut proportionally, then trim to fit
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= content_budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + marker


def message_tokens(message) -> int:
    total = estimate_tokens(message.content or "") + 4
    if getattr(message, "tool_calls", None):
        total += estimate_tokens(json.dumps(message.tool_calls, ensure_ascii=False))
    if getattr(message, "tool_call_id", None):
        total += estimate_tokens(message.tool_call_id)
    return total


def estimate_input_tokens(messages: list, tools: list[dict] | None = None) -> int:
    return sum(message_tokens(m) for m in messages) + (
        estimate_tokens(json.dumps(tools, ensure_ascii=False)) + 4 if tools else 0
    )


def trim_messages(
    messages: list, max_input_tokens: int | None = None, *, tools: list[dict] | None = None,
) -> list:
    """Return a new message list fitting the budget.

    Keeps system instructions, the current user request and the final group.
    Drops oldest history first. An assistant message
    carrying tool_calls and its following role=="tool" replies are treated
    as one atomic group (OpenAI API rejects orphaned tool messages).
    """
    if max_input_tokens is None:
        return messages
    if not messages:
        return messages

    total = estimate_input_tokens(messages, tools)
    if total <= max_input_tokens:
        return messages

    head = []
    body = list(messages)
    while body and body[0].role == "system":
        head.append(body.pop(0))

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

    current_user = next(
        (idx for idx in range(len(groups) - 1, -1, -1) if groups[idx][0].role == "user"),
        None,
    )
    required = {len(groups) - 1}
    if current_user is not None:
        required.add(current_user)
    kept = {idx: list(groups[idx]) for idx in required if idx >= 0}

    def flattened():
        return head + [m for idx in sorted(kept) for m in kept[idx]]

    # Tool results are data, so they can be shortened. Never rewrite system
    # instructions, the user's request, or JSON tool-call arguments to fit.
    excess = estimate_input_tokens(flattened(), tools) - max_input_tokens
    if excess > 0:
        for idx in sorted(kept):
            for pos, message in enumerate(kept[idx]):
                if message.role != "tool":
                    continue
                content = truncate_text(message.content or "", max(0, estimate_tokens(message.content or "") - excess))
                kept[idx][pos] = replace(message, content=content)
                excess = estimate_input_tokens(flattened(), tools) - max_input_tokens
                if excess <= 0:
                    break
            if excess <= 0:
                break
    if excess > 0:
        raise ContextBudgetExceeded("Required conversation context exceeds the configured input token budget")

    budget = max_input_tokens - estimate_input_tokens(flattened(), tools)
    for idx in range(len(groups) - 1, -1, -1):
        if idx in required:
            continue
        cost = sum(message_tokens(m) for m in groups[idx])
        if cost > budget:
            break  # retain a contiguous recent window, not disconnected old turns
        kept[idx] = groups[idx]
        budget -= cost
    return flattened()
