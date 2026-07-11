"""Audit API — trace replay and metrics dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.models.agent import Agent
from server.models.audit import AuditTrace
from server.middleware.auth import get_current_user, get_tenant_id

router = APIRouter(dependencies=[Depends(get_current_user)])

RETRIEVAL_EVENT_TYPES = ("retrieval", "pre_retrieval")


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Get all events for a trace — full pipeline replay."""
    result = await db.execute(
        select(AuditTrace)
        .where(AuditTrace.trace_id == trace_id, AuditTrace.tenant_id == tenant_id)
        .order_by(AuditTrace.timestamp)
    )
    traces = result.scalars().all()
    return [
        {
            "id": t.id,
            "trace_id": t.trace_id,
            "session_id": t.session_id,
            "agent_id": t.agent_id,
            "event_type": t.event_type,
            "event_data": t.event_data,
            "retrieval_hits": t.retrieval_hits,
            "llm_meta": t.llm_meta,
            "tool_meta": t.tool_meta,
            "workflow_meta": t.workflow_meta,
            "escalation_reason": t.escalation_reason,
            "latency_ms": t.latency_ms,
            "timestamp": t.timestamp.isoformat() if t.timestamp else None,
        }
        for t in traces
    ]


@router.get("/sessions/{session_id}/traces")
async def get_session_traces(
    session_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Get all traces for a session — conversation replay."""
    result = await db.execute(
        select(AuditTrace)
        .where(AuditTrace.session_id == session_id, AuditTrace.tenant_id == tenant_id)
        .order_by(AuditTrace.timestamp)
    )
    traces = result.scalars().all()
    return [
        {
            "id": t.id,
            "trace_id": t.trace_id,
            "event_type": t.event_type,
            "event_data": t.event_data,
            "latency_ms": t.latency_ms,
            "timestamp": t.timestamp.isoformat() if t.timestamp else None,
        }
        for t in traces
    ]


@router.get("/traces")
async def list_traces(
    tenant_id: str = Depends(get_tenant_id),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List recent audit traces with pagination."""
    stmt = (
        select(AuditTrace)
        .where(AuditTrace.tenant_id == tenant_id)
    )
    if event_type:
        stmt = stmt.where(AuditTrace.event_type == event_type)

    # Count total
    count_stmt = select(func.count(AuditTrace.id)).where(AuditTrace.tenant_id == tenant_id)
    if event_type:
        count_stmt = count_stmt.where(AuditTrace.event_type == event_type)
    total_q = await db.execute(count_stmt)
    total = total_q.scalar() or 0

    # Fetch page
    stmt = stmt.order_by(AuditTrace.timestamp.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    traces = result.scalars().all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "id": t.id,
                "trace_id": t.trace_id,
                "session_id": t.session_id,
                "agent_id": t.agent_id,
                "event_type": t.event_type,
                "latency_ms": t.latency_ms,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
            }
            for t in traces
        ],
    }


@router.get("/metrics")
async def get_metrics(
    tenant_id: str = Depends(get_tenant_id),
    hours: int = Query(default=24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
):
    """Dashboard metrics: latency, hit rates, tool success, escalation rate."""
    since = datetime.utcnow() - timedelta(hours=hours)
    base_filter = [AuditTrace.tenant_id == tenant_id, AuditTrace.timestamp >= since]

    # Total invocations
    total_q = await db.execute(
        select(func.count(AuditTrace.id)).where(
            AuditTrace.event_type == "user_input", *base_filter
        )
    )
    total_invocations = total_q.scalar() or 0

    # Retrieval events
    retrieval_q = await db.execute(
        select(func.count(AuditTrace.id)).where(
            AuditTrace.event_type.in_(RETRIEVAL_EVENT_TYPES), *base_filter
        )
    )
    retrieval_count = retrieval_q.scalar() or 0

    # Avg retrieval latency
    avg_retrieval_lat = await db.execute(
        select(func.avg(AuditTrace.latency_ms)).where(
            AuditTrace.event_type.in_(RETRIEVAL_EVENT_TYPES), *base_filter
        )
    )
    avg_retrieval_latency = avg_retrieval_lat.scalar() or 0

    # Avg LLM latency
    avg_llm_lat = await db.execute(
        select(func.avg(AuditTrace.latency_ms)).where(
            AuditTrace.event_type == "llm_call", *base_filter
        )
    )
    avg_llm_latency = avg_llm_lat.scalar() or 0

    # Tool calls
    tool_total_q = await db.execute(
        select(func.count(AuditTrace.id)).where(
            AuditTrace.event_type == "tool_call", *base_filter
        )
    )
    tool_total = tool_total_q.scalar() or 0

    # Escalations
    escalation_q = await db.execute(
        select(func.count(AuditTrace.id)).where(
            AuditTrace.event_type == "escalation", *base_filter
        )
    )
    escalation_count = escalation_q.scalar() or 0

    # Risk blocks
    risk_q = await db.execute(
        select(func.count(AuditTrace.id)).where(
            AuditTrace.event_type == "risk_block", *base_filter
        )
    )
    risk_block_count = risk_q.scalar() or 0

    return {
        "period_hours": hours,
        "total_invocations": total_invocations,
        "retrieval_count": retrieval_count,
        "avg_retrieval_latency_ms": round(avg_retrieval_latency, 2),
        "avg_llm_latency_ms": round(avg_llm_latency, 2),
        "tool_call_count": tool_total,
        "escalation_count": escalation_count,
        "escalation_rate": round(escalation_count / max(total_invocations, 1) * 100, 2),
        "risk_block_count": risk_block_count,
    }


@router.get("/usage")
async def get_usage(
    tenant_id: str = Depends(get_tenant_id),
    days: int = Query(default=30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Business-facing token usage summary for the trailing `days` window.

    Aggregates `llm_call` audit events (see agent_runtime.py's per-round
    `audit.log("llm_call", llm_meta={...})` call, which now carries
    prompt_tokens/completion_tokens/total_tokens alongside model/round).
    Rows written before token tracking existed, or produced by a streaming
    response whose provider never echoed a `usage` block on the final SSE
    chunk, simply contribute 0 tokens — they still count toward
    `total_invocations` so the numbers never look broken, just incomplete.

    Aggregation happens in Python (not a DB-side JSON extraction) so this
    works identically on SQLite (dev) and PostgreSQL (prod); the 90-day cap
    plus the audit retention job (server/engine/retention.py) keep the
    scanned row count bounded.
    """
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(AuditTrace.agent_id, AuditTrace.llm_meta, AuditTrace.timestamp).where(
            AuditTrace.tenant_id == tenant_id,
            AuditTrace.event_type == "llm_call",
            AuditTrace.timestamp >= since,
        )
    )
    rows = result.all()

    total_invocations = 0
    total_prompt = 0
    total_completion = 0
    by_day: dict[str, dict[str, int]] = {}
    by_agent: dict[str, dict[str, int]] = {}

    for agent_id, llm_meta, timestamp in rows:
        total_invocations += 1
        meta = llm_meta or {}
        prompt = int(meta.get("prompt_tokens") or 0)
        completion = int(meta.get("completion_tokens") or 0)
        total_prompt += prompt
        total_completion += completion
        tokens = prompt + completion

        day_key = (timestamp or datetime.utcnow()).strftime("%Y-%m-%d")
        day_entry = by_day.setdefault(day_key, {"invocations": 0, "tokens": 0})
        day_entry["invocations"] += 1
        day_entry["tokens"] += tokens

        agent_entry = by_agent.setdefault(agent_id, {"invocations": 0, "tokens": 0})
        agent_entry["invocations"] += 1
        agent_entry["tokens"] += tokens

    # Resolve display names for the agents that actually appear in this
    # window (best-effort — a deleted agent just falls back to its raw id).
    agent_names: dict[str, str] = {}
    if by_agent:
        name_rows = await db.execute(
            select(Agent.id, Agent.name).where(Agent.id.in_(list(by_agent.keys())))
        )
        agent_names = dict(name_rows.all())

    return {
        "period_days": days,
        "total_invocations": total_invocations,
        "total_tokens": total_prompt + total_completion,
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "by_day": [
            {"date": day, "invocations": v["invocations"], "tokens": v["tokens"]}
            for day, v in sorted(by_day.items())
        ],
        "by_agent": [
            {
                "agent_id": agent_id,
                "agent_name": agent_names.get(agent_id, agent_id),
                "invocations": v["invocations"],
                "tokens": v["tokens"],
            }
            for agent_id, v in sorted(by_agent.items(), key=lambda kv: -kv[1]["tokens"])
        ],
    }
