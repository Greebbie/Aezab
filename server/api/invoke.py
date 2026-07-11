"""The /invoke endpoint — single Headless API entry point."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.db import get_db
from server.middleware.auth import get_current_user, get_tenant_id
from server.models.agent import Agent
from server.exceptions import (
    AezabError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    RetrievalError,
    ToolInvocationError,
    WorkflowError,
)
from server.schemas.invoke import InvokeRequest, InvokeResponse
from server.engine.agent_runtime import AgentRuntime
from server.engine.request_guard import (
    get_idempotent_response,
    session_lock,
    store_idempotent_response,
)

router = APIRouter(dependencies=[Depends(get_current_user)])
logger = logging.getLogger(__name__)


def _is_cacheable(response: InvokeResponse) -> bool:
    """A response is safe to replay to an idempotent retry only if it is a
    genuine result — never a degraded/classified-failure fallback (those carry
    `degraded` or `error_detail` in metadata) which must re-run on retry."""
    meta = response.metadata or {}
    return not (meta.get("degraded") or meta.get("error_detail"))


async def _check_agent_tenant(db: AsyncSession, agent_id: str, tenant_id: str) -> None:
    """404 if the agent exists but belongs to another tenant.

    In HLAB_DISABLE_AUTH dev mode, ownership checks are bypassed entirely so
    every agent remains reachable regardless of its tenant_id.
    """
    if settings.disable_auth:
        return
    result = await db.execute(select(Agent.tenant_id).where(Agent.id == agent_id))
    row = result.scalar_one_or_none()
    if row is not None and row != tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/invoke", response_model=InvokeResponse)
async def invoke(
    req: InvokeRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Unified entry point: send a message to any configured agent.

    This is the single API that all client systems integrate with.
    Handles both QA (knowledge retrieval) and workflow (process execution) scenarios.

    Supports an optional client `Idempotency-Key` header (sync-only — the
    streaming endpoint below is never cached): a repeated request with the
    same key + same tenant within `settings.idempotency_ttl_s` returns the
    previously computed response without re-running the pipeline (no
    duplicate LLM spend, no duplicate workflow side-effects). This is a
    best-effort, per-process guard against accidental client retries — see
    server/engine/request_guard.py for the multi-worker caveat.
    """
    cached = get_idempotent_response(tenant_id, idempotency_key)
    if cached is not None:
        return InvokeResponse(**cached)

    await _check_agent_tenant(db, req.agent_id, tenant_id)
    runtime = AgentRuntime(db)
    # Serialize concurrent calls for the same session_id. When there is no
    # session yet but a client sent an Idempotency-Key, serialize on the key
    # instead, so two concurrent same-key retries can't both run the pipeline.
    lock_key = req.session_id or (
        f"idem:{tenant_id}:{idempotency_key}" if idempotency_key else None
    )
    try:
        async with session_lock(lock_key):
            # Re-check inside the lock: a concurrent same-key retry that raced
            # the first request past the pre-lock check would otherwise run the
            # pipeline (and its side effects) a second time. The lock serializes
            # same-session retries, so the first one has now cached its result.
            cached = get_idempotent_response(tenant_id, idempotency_key)
            if cached is not None:
                return InvokeResponse(**cached)
            response = await asyncio.wait_for(
                runtime.invoke(req), timeout=settings.pipeline_timeout_seconds,
            )
            # Only cache genuine results. A degraded/failed turn (classified LLM
            # error, silent fallback) must NOT be replayed to a legitimate
            # client retry — that would pin a stale apology for the whole TTL
            # and, worse, mask a workflow submission that never really ran.
            if _is_cacheable(response):
                store_idempotent_response(
                    tenant_id, idempotency_key,
                    response.model_dump(), settings.idempotency_ttl_s,
                )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="请求处理超时，请稍后重试。",
        )

    return response


@router.post("/invoke/stream")
async def invoke_stream(
    req: InvokeRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    """SSE streaming entry point: same logic as /invoke, with real-time progress events.

    Emits Server-Sent Events:
      - event: answer_delta -> {"text": "<incremental token(s)>"}
      - event: answer_reset -> {} (discard any streamed text accumulated so
                                far this turn — it was a tool-call round's
                                throwaway pre-tool content, or the stream
                                failed mid-way and a non-streaming fallback
                                is about to produce the real answer)
      - event: status       -> {"stage": "retrieval", "state": "started"|"finished", ...}
                                {"stage": "tool", "name": "...", "state": "started"|"finished"}
      - event: answer        -> {"content": "<final answer>"}
      - event: done          -> {"session_id": "...", "trace_id": "...", "citations": [...], "followups": [...]}
      - event: error         -> {"detail": "...", "error_type": "...", "error_msg": "..."}

    `answer_delta`/`status`/`answer_reset` events are best-effort progress;
    clients that ignore them and only read `answer`/`done` keep working
    exactly as before (the final `answer` text is authoritative and always
    REPLACES, not appends to, any streamed partial text — it may differ from
    the concatenation of deltas when fallbacks/refusal-supplements fire).
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _sse_event(event: str, data: dict) -> str:
        """Format a single SSE event string."""
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {payload}\n\n"

    async def _event_cb(event_type: str, data: dict) -> None:
        """Map an agent-runtime pipeline event onto an SSE event."""
        if event_type == "answer_delta":
            await queue.put(_sse_event("answer_delta", {"text": data.get("text", "")}))
        elif event_type == "answer_reset":
            await queue.put(_sse_event("answer_reset", {}))
        elif event_type == "tool_call_started":
            await queue.put(_sse_event("status", {
                "stage": "tool", "name": data.get("name"), "state": "started",
            }))
        elif event_type == "tool_call_finished":
            await queue.put(_sse_event("status", {
                "stage": "tool", "name": data.get("name"), "state": "finished",
            }))
        elif event_type == "pre_retrieval":
            await queue.put(_sse_event("status", {
                "stage": "retrieval", "state": "started", "domains": data.get("domains", []),
            }))
        elif event_type == "pre_retrieval_done":
            await queue.put(_sse_event("status", {
                "stage": "retrieval", "state": "finished", "hits": data.get("hits", 0),
            }))

    async def _run_pipeline() -> None:
        """Execute the agent pipeline and push SSE events onto the queue."""
        try:
            await _check_agent_tenant(db, req.agent_id, tenant_id)

            runtime = AgentRuntime(db)

            # Run the full orchestration pipeline (bounded by the global timeout).
            # Serialized per session_id like the sync /invoke path (no-op for
            # a brand-new session_id=None). Streaming responses are never
            # idempotency-cached (see request_guard.py docstring).
            try:
                async with session_lock(req.session_id):
                    response: InvokeResponse = await asyncio.wait_for(
                        runtime.invoke(req, event_cb=_event_cb), timeout=settings.pipeline_timeout_seconds,
                    )
            except asyncio.TimeoutError:
                logger.error("SSE pipeline timed out after %ss", settings.pipeline_timeout_seconds)
                await queue.put(_sse_event("error", {
                    "detail": "pipeline timeout",
                    "error_type": "timeout",
                    "error_msg": "请求处理超时，请稍后重试。",
                }))
                return

            # Emit the final answer
            await queue.put(_sse_event("answer", {"content": response.short_answer}))

            # Emit the done event with metadata
            await queue.put(_sse_event("done", {
                "session_id": response.session_id,
                "trace_id": response.trace_id,
                "citations": [c.model_dump() for c in response.citations],
                "followups": response.suggested_followups,
                "expanded_answer": response.expanded_answer,
                "workflow_card": response.workflow_card.model_dump() if response.workflow_card else None,
                "workflow_status": response.workflow_status,
                "escalated": response.escalated,
                "escalation_reason": response.escalation_reason,
                "skill_info": response.skill_info,
                "metadata": response.metadata,
            }))

        except LLMTimeoutError as exc:
            logger.error("SSE pipeline LLM timeout: %s", exc, exc_info=True)
            await queue.put(_sse_event("error", {
                "detail": str(exc),
                "error_type": "timeout",
                "error_msg": "请求超时，模型处理较慢，请稍后重试。",
            }))
        except LLMRateLimitError as exc:
            logger.error("SSE pipeline LLM rate limit: %s", exc, exc_info=True)
            await queue.put(_sse_event("error", {
                "detail": str(exc),
                "error_type": "rate_limit",
                "error_msg": "请求频率过高，请稍后重试。",
            }))
        except LLMError as exc:
            logger.error("SSE pipeline LLM error: %s", exc, exc_info=True)
            await queue.put(_sse_event("error", {
                "detail": str(exc),
                "error_type": "llm_error",
                "error_msg": "语言模型服务异常，请检查配置后重试。",
            }))
        except RetrievalError as exc:
            logger.error("SSE pipeline retrieval error: %s", exc, exc_info=True)
            await queue.put(_sse_event("error", {
                "detail": str(exc),
                "error_type": "retrieval",
                "error_msg": "知识检索服务异常，请稍后重试。",
            }))
        except WorkflowError as exc:
            logger.error("SSE pipeline workflow error: %s", exc, exc_info=True)
            await queue.put(_sse_event("error", {
                "detail": str(exc),
                "error_type": "workflow",
                "error_msg": "流程执行异常，请检查流程配置。",
            }))
        except ToolInvocationError as exc:
            logger.error("SSE pipeline tool error: %s", exc, exc_info=True)
            await queue.put(_sse_event("error", {
                "detail": str(exc),
                "error_type": "tool",
                "error_msg": "工具调用失败，请检查工具配置。",
            }))
        except AezabError as exc:
            logger.error("SSE pipeline Aezab error: %s", exc, exc_info=True)
            await queue.put(_sse_event("error", {
                "detail": str(exc),
                "error_type": "platform",
                "error_msg": exc.message,
            }))
        except HTTPException as exc:
            logger.warning("SSE pipeline rejected: %s", exc.detail)
            await queue.put(_sse_event("error", {
                "detail": str(exc.detail),
                "error_type": "not_found" if exc.status_code == 404 else "request",
                "error_msg": str(exc.detail),
            }))
        except Exception as exc:
            logger.error("SSE pipeline unexpected error: %s", exc, exc_info=True)
            await queue.put(_sse_event("error", {
                "detail": str(exc),
                "error_type": "internal",
                "error_msg": "系统暂时无法响应，请稍后重试或联系人工客服。",
            }))
        finally:
            # Sentinel: signals the generator to stop
            await queue.put(None)

    async def _event_generator():
        """Async generator that yields SSE events from the queue."""
        # Start the pipeline as a background task
        task = asyncio.create_task(_run_pipeline())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            # Ensure the task is cleaned up if the client disconnects
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
