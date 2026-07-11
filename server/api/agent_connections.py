"""Agent connection API — manage inter-agent collaboration relationships."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.models.agent import Agent
from server.models.agent_connection import AgentConnection
from server.schemas.skill import AgentConnectionCreate, AgentConnectionUpdate, AgentConnectionOut
from server.middleware.auth import get_current_user, get_tenant_id

router = APIRouter(dependencies=[Depends(get_current_user)])

VALID_CONNECTION_TYPES = {"delegate", "orchestrate", "peer"}


@router.get("/", response_model=list[AgentConnectionOut])
async def list_connections(
    tenant_id: str = Depends(get_tenant_id),
    agent_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List connections, optionally filtered by agent (as source or target)."""
    query = select(AgentConnection).where(AgentConnection.tenant_id == tenant_id)
    if agent_id:
        query = query.where(
            or_(
                AgentConnection.source_agent_id == agent_id,
                AgentConnection.target_agent_id == agent_id,
            )
        )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/", response_model=AgentConnectionOut, status_code=201)
async def create_connection(
    body: AgentConnectionCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    if body.connection_type not in VALID_CONNECTION_TYPES:
        raise HTTPException(400, f"Invalid connection_type. Must be one of: {VALID_CONNECTION_TYPES}")

    if body.source_agent_id == body.target_agent_id:
        raise HTTPException(400, "Cannot connect an agent to itself")

    # Both endpoints of the connection must belong to the caller's tenant.
    result = await db.execute(
        select(Agent).where(
            Agent.id.in_([body.source_agent_id, body.target_agent_id]),
            Agent.tenant_id == tenant_id,
        )
    )
    owned_ids = {a.id for a in result.scalars().all()}
    if body.source_agent_id not in owned_ids or body.target_agent_id not in owned_ids:
        raise HTTPException(404, "Agent not found")

    conn = AgentConnection(
        source_agent_id=body.source_agent_id,
        target_agent_id=body.target_agent_id,
        connection_type=body.connection_type,
        shared_context=body.shared_context,
        description=body.description,
        enabled=body.enabled,
        tenant_id=tenant_id,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


@router.get("/{connection_id}", response_model=AgentConnectionOut)
async def get_connection(
    connection_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentConnection).where(
            AgentConnection.id == connection_id, AgentConnection.tenant_id == tenant_id
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    return conn


@router.put("/{connection_id}", response_model=AgentConnectionOut)
async def update_connection(
    connection_id: str,
    body: AgentConnectionUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentConnection).where(
            AgentConnection.id == connection_id, AgentConnection.tenant_id == tenant_id
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")

    update_data = body.model_dump(exclude_unset=True)
    if "connection_type" in update_data and update_data["connection_type"] not in VALID_CONNECTION_TYPES:
        raise HTTPException(400, f"Invalid connection_type. Must be one of: {VALID_CONNECTION_TYPES}")

    for key, value in update_data.items():
        setattr(conn, key, value)

    await db.commit()
    await db.refresh(conn)
    return conn


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentConnection).where(
            AgentConnection.id == connection_id, AgentConnection.tenant_id == tenant_id
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    await db.delete(conn)
    await db.commit()
