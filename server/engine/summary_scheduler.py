"""Async rolling-summary scheduler — folds old conversation turns into
``session.context["summary"]`` OFF the request/response critical path.

Before this module existed, the fold ran inline inside
``AgentRuntime._invoke_conversational``: every turn, once a session passed
``SUMMARY_THRESHOLD`` message rows, the pipeline paid for a full extra LLM
round-trip *before* the user got an answer. Now the fold happens in the
background — the current turn's answer returns immediately using whatever
summary a *prior* turn already persisted (see
``AgentRuntime._get_persisted_summary``), and this module schedules a
background task that folds the newly-finished turn's rows so the *next*
turn's prompt sees them. A one-turn lag is by design and acceptable for a
rolling summary.

``schedule_summary_update`` mirrors ``server.engine.event_dispatcher.emit_event``'s
fire-and-forget shape: a synchronous, non-raising call that schedules an
``asyncio`` task via ``create_task``, holds a strong reference in a
module-level set (``create_task`` only keeps a *weak* reference internally,
so an unreferenced task can be garbage-collected mid-await), and discards
that reference via a ``done_callback``.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from server.config import env_str
from server.db import async_session
from server.engine.llm_adapter import LLMMessage, get_llm_adapter_for_agent
from server.engine.request_guard import session_lock
from server.models.agent import Agent
from server.models.session import ConversationSession, Message

logger = logging.getLogger(__name__)

# ── Current-session memory (Wave 4 / Workstream M; moved off the critical
# path in Wave 5 / Workstream B) ────────────────────────────────────
# Rolling-summary trigger, in message ROWS (each user turn writes one "user"
# row and one "assistant" row to `messages`, i.e. 2 rows/turn — this is NOT
# `session.message_count`, which counts turns). Once the session's total row
# count reaches this threshold, older-than-recent-window rows get folded into
# session.context["summary"] instead of being dropped outright. Default 12
# rows ~= 6 turns. Env: AEZAB_SUMMARY_THRESHOLD (legacy HLAB_ accepted).
SUMMARY_THRESHOLD = int(env_str("SUMMARY_THRESHOLD", "12"))
# How many of the most recent raw Message rows (not turns) stay verbatim in
# the prompt (everything older, if any, is represented only via the rolling
# summary). Env: AEZAB_RECENT_WINDOW (legacy HLAB_ accepted).
RECENT_WINDOW = int(env_str("RECENT_WINDOW", "6"))

# In-flight session_ids currently being folded — dedupes concurrent
# schedule_summary_update() calls for the same session. Guarded by a
# short-held threading.Lock (never held across an `await`), mirroring the
# lock discipline used by server/engine/request_guard.py.
_inflight_guard = threading.Lock()
_inflight_sessions: set[str] = set()

# Strong references to in-flight background tasks — see module docstring.
_background_tasks: set[asyncio.Task] = set()


def schedule_summary_update(session_id: str, agent_id: str) -> None:
    """Fire-and-forget: schedule a background rolling-summary fold for
    `session_id`. Synchronous and non-raising by design so callers in the
    request hot path never need to await or handle fold failures.

    Dedupe: if a fold is already in flight for this session, this is a
    no-op — the next turn will schedule its own fold, and the
    `summarized_upto` watermark makes folding idempotent, so skipping here
    never loses data, only delays it by one more turn.
    """
    with _inflight_guard:
        if session_id in _inflight_sessions:
            return
        _inflight_sessions.add(session_id)

    async def _run() -> None:
        try:
            await _fold_session_summary(session_id, agent_id)
        except Exception as e:  # noqa: BLE001 - background task must never raise
            logger.warning(
                "summary_scheduler: fold failed for session %s: %s",
                session_id, e, exc_info=True,
            )
        finally:
            with _inflight_guard:
                _inflight_sessions.discard(session_id)

    try:
        task = asyncio.create_task(_run())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except RuntimeError:
        # No running event loop in this context — never happens from async
        # request handlers, but guard anyway so this never raises. Clear the
        # in-flight marker we just set since no task will ever clear it.
        logger.warning(
            "schedule_summary_update: no running event loop; dropping fold "
            "for session %s", session_id,
        )
        with _inflight_guard:
            _inflight_sessions.discard(session_id)


async def _fold_session_summary(session_id: str, agent_id: str) -> None:
    """Task body, in three phases so the expensive work runs LOCK-FREE and
    only the final persist serializes against concurrent invokes:

      Phase 1 (no lock): open own async_session, load session/agent/full
        history, re-apply the exact gate `AgentRuntime._maybe_update_summary`
        used to apply inline, compute the fold boundary + `to_fold` slice.
      Phase 2 (no lock): the one-shot LLM fold call.
      Phase 3 (under `session_lock(session_id)`, fresh async_session):
        reload the session, RE-CHECK the watermark, persist, commit.

    Why the lock covers only the persist: holding it across the LLM call
    would block the user's NEXT /invoke for this session (server/api/invoke.py
    takes the same lock) for up to a full LLM round-trip — exactly the
    latency B-T1 exists to remove. The lock isn't needed earlier anyway:
    phases 1-2 only READ messages (a concurrent invoke appending rows merely
    shifts where a later fold's boundary lands — harmless), and only this
    scheduler ever WRITES `session.context`. The watermark re-check in
    phase 3 discards this fold's result if another fold already covered
    these rows in the meantime (the `summarized_upto` watermark makes
    folding idempotent, so discarding a stale result loses nothing — the
    next turn re-schedules and catches up); the in-flight dedupe set in
    `schedule_summary_update` already prevents two concurrent folds per
    session in-process, so the re-check is belt-and-suspenders.

    Any failure propagates to the caller in `schedule_summary_update`,
    which logs and swallows it."""
    # ── Phase 1 (no lock): load + gate + compute the fold slice ──
    async with async_session() as db:
        session = await db.get(ConversationSession, session_id)
        if session is None:
            return
        agent = await db.get(Agent, agent_id)
        if agent is None:
            return

        context = session.context or {}
        existing_summary = context.get("summary") or None

        all_msgs = await _get_all_history(db, session_id)
        total = len(all_msgs)
        # Authoritative gate: actual row count vs. the threshold (rows).
        if total < SUMMARY_THRESHOLD:
            return
        if total <= RECENT_WINDOW:
            return

        summarized_upto = context.get("summarized_upto") or 0
        boundary = total - RECENT_WINDOW
        if boundary <= summarized_upto:
            return

        to_fold = all_msgs[summarized_upto:boundary]
        if not to_fold:
            return

        # ── Phase 2 (no lock): the expensive LLM fold ──
        new_summary = await _summarize_messages_llm(
            db, agent, existing_summary or "", to_fold,
        )
        if not new_summary:
            return

    # ── Phase 3 (locked): re-check watermark, then persist ──
    async with session_lock(session_id):
        async with async_session() as db:
            session = await db.get(ConversationSession, session_id)
            if session is None:
                return
            current_upto = (session.context or {}).get("summarized_upto") or 0
            if current_upto >= boundary:
                # Another fold already covered these rows between phase 1
                # and now — this result is stale; discard it.
                return
            session.context = {"summary": new_summary, "summarized_upto": boundary}
            flag_modified(session, "context")
            await db.commit()


async def _get_all_history(db, session_id: str) -> list[Message]:
    """Full message history, oldest first — the rolling summary needs to see
    everything older than the recent window."""
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return list(result.scalars().all())


async def _summarize_messages_llm(
    db, agent: Agent, prior_summary: str, messages: list[Message],
) -> str:
    """One-shot LLM call: fold `messages` onto `prior_summary`.

    Raises on failure; the caller (`_fold_session_summary`, via
    `schedule_summary_update`) catches, logs, and leaves the persisted
    summary untouched.
    """
    convo_text = "\n".join(f"{m.role}: {m.content}" for m in messages)
    prompt = (
        "You maintain a running summary of an ongoing customer-service "
        "conversation. Fold the NEW messages below into the EXISTING "
        "summary, producing one updated summary in Chinese. Keep it "
        "concise (a few sentences), preserve concrete facts (names, "
        "numbers, decisions, open questions), and drop small talk.\n\n"
        f"Existing summary: {prior_summary or '(none yet)'}\n\n"
        f"New messages:\n{convo_text}\n\n"
        "Output ONLY the updated summary text, nothing else."
    )
    llm = await get_llm_adapter_for_agent(agent, db)
    resp = await llm.chat(
        [
            LLMMessage(
                role="system",
                content="Summarize conversation history concisely in Chinese.",
            ),
            LLMMessage(role="user", content=prompt),
        ],
        temperature=0.0,
        max_tokens=400,
    )
    return (resp.content or "").strip()
