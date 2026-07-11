"""Agent-Skill binding API — manage which skills are bound to an agent."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.models.agent import Agent
from server.models.agent_skill import AgentSkill
from server.models.skill import Skill
from server.schemas.skill import AgentSkillCreate, AgentSkillOut
from server.middleware.auth import get_current_user, get_tenant_id

router = APIRouter(dependencies=[Depends(get_current_user)])


async def _get_owned_agent(agent_id: str, tenant_id: str, db: AsyncSession) -> Agent:
    """Fetch an agent, scoped to the caller's tenant, or raise 404."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


@router.get("/{agent_id}/skills", response_model=list[AgentSkillOut])
async def list_agent_skills(
    agent_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """List all skills bound to an agent, enriched with skill metadata."""
    await _get_owned_agent(agent_id, tenant_id, db)

    result = await db.execute(
        select(AgentSkill).where(AgentSkill.agent_id == agent_id)
    )
    bindings = result.scalars().all()

    # Enrich with skill info
    skill_ids = [b.skill_id for b in bindings]
    if skill_ids:
        skill_result = await db.execute(
            select(Skill).where(Skill.id.in_(skill_ids))
        )
        skill_map = {s.id: s for s in skill_result.scalars().all()}
    else:
        skill_map = {}

    out = []
    for b in bindings:
        s = skill_map.get(b.skill_id)
        out.append(AgentSkillOut(
            id=b.id,
            agent_id=b.agent_id,
            skill_id=b.skill_id,
            priority_override=b.priority_override,
            config_override=b.config_override,
            enabled=b.enabled,
            created_at=b.created_at,
            skill_name=s.name if s else None,
            skill_type=s.skill_type if s else None,
            skill_description=s.description if s else None,
        ))
    return out


@router.post("/{agent_id}/skills", response_model=AgentSkillOut, status_code=201)
async def bind_skill(
    agent_id: str,
    body: AgentSkillCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Bind a skill to an agent."""
    await _get_owned_agent(agent_id, tenant_id, db)

    # Check skill exists and belongs to the caller's tenant
    result = await db.execute(
        select(Skill).where(Skill.id == body.skill_id, Skill.tenant_id == tenant_id)
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "Skill not found")

    # Check for duplicate binding
    result = await db.execute(
        select(AgentSkill).where(
            AgentSkill.agent_id == agent_id,
            AgentSkill.skill_id == body.skill_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(409, "Skill already bound to this agent")

    binding = AgentSkill(
        agent_id=agent_id,
        skill_id=body.skill_id,
        priority_override=body.priority_override,
        config_override=body.config_override,
        enabled=body.enabled,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)

    return AgentSkillOut(
        id=binding.id,
        agent_id=binding.agent_id,
        skill_id=binding.skill_id,
        priority_override=binding.priority_override,
        config_override=binding.config_override,
        enabled=binding.enabled,
        created_at=binding.created_at,
        skill_name=skill.name,
        skill_type=skill.skill_type,
        skill_description=skill.description,
    )


@router.delete("/{agent_id}/skills/{binding_id}", status_code=204)
async def unbind_skill(
    agent_id: str,
    binding_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Remove a skill binding from an agent."""
    await _get_owned_agent(agent_id, tenant_id, db)

    result = await db.execute(
        select(AgentSkill).where(
            AgentSkill.id == binding_id,
            AgentSkill.agent_id == agent_id,
        )
    )
    binding = result.scalar_one_or_none()
    if not binding:
        raise HTTPException(404, "Binding not found")
    await db.delete(binding)
    await db.commit()
