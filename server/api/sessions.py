"""Session management API — list/inspect/delete conversation sessions.

Read-only + delete surface for the external-app integration layer. There is
no dedicated title column (no migration infra in this project yet), so the
list endpoint derives a display title from the session's first user message
(first 40 characters) via a correlated scalar subquery.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.middleware.auth import get_current_user, get_tenant_id
from server.models.session import ConversationSession, Message

router = APIRouter(dependencies=[Depends(get_current_user)])

_TITLE_MAX_CHARS = 40


def _title_subquery():
    """Scalar subquery: first 40 chars of the session's earliest user message."""
    return (
        select(Message.content)
        .where(
            Message.session_id == ConversationSession.id,
            Message.role == "user",
        )
        .order_by(Message.created_at.asc())
        .limit(1)
        .correlate(ConversationSession)
        .scalar_subquery()
    )


def _derive_title(raw_content: str | None) -> str | None:
    if not raw_content:
        return None
    return raw_content.strip()[:_TITLE_MAX_CHARS] or None


async def _get_owned_session(
    db: AsyncSession, session_id: str, tenant_id: str,
) -> ConversationSession:
    """Fetch a ConversationSession, 404ing if missing or owned by another tenant."""
    result = await db.execute(
        select(ConversationSession).where(
            ConversationSession.id == session_id,
            ConversationSession.tenant_id == tenant_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    return session


@router.get("/")
async def list_sessions(
    agent_id: str | None = None,
    user_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """List conversation sessions for the caller's tenant, newest first."""
    base_filter = [ConversationSession.tenant_id == tenant_id]
    if agent_id:
        base_filter.append(ConversationSession.agent_id == agent_id)
    if user_id:
        base_filter.append(ConversationSession.user_id == user_id)

    count_stmt = select(func.count(ConversationSession.id)).where(*base_filter)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(ConversationSession, _title_subquery().label("title"))
        .where(*base_filter)
        .order_by(
            ConversationSession.updated_at.desc(),
            ConversationSession.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)

    items = [
        {
            "id": s.id,
            "agent_id": s.agent_id,
            "user_id": s.user_id,
            "status": s.status,
            "message_count": s.message_count,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "title": _derive_title(title),
        }
        for s, title in result.all()
    ]

    return {"total": total, "offset": offset, "limit": limit, "items": items}


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Chronological message history for a single session."""
    await _get_owned_session(db, session_id, tenant_id)

    count_stmt = select(func.count(Message.id)).where(Message.session_id == session_id)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "short_answer": m.short_answer,
                "expanded_answer": m.expanded_answer,
                "citations": m.citations,
                "suggested_followups": m.suggested_followups,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Delete a session and all of its messages."""
    session = await _get_owned_session(db, session_id, tenant_id)

    await db.execute(delete(Message).where(Message.session_id == session_id))
    await db.delete(session)
    await db.commit()
