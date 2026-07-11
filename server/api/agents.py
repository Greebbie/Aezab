"""Agent management CRUD API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.middleware.auth import get_current_user, get_tenant_id
from server.models.agent import Agent
from server.models.skill import Skill
from server.models.agent_skill import AgentSkill
from server.schemas.agent import AgentCreate, AgentUpdate, AgentOut

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/", response_model=list[AgentOut])
async def list_agents(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.tenant_id == tenant_id))
    return result.scalars().all()


@router.post("/", response_model=AgentOut, status_code=201)
async def create_agent(
    body: AgentCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    agent = Agent(
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        llm_model=body.llm_model,
        llm_config_id=body.llm_config_id,
        response_config=body.response_config,
        risk_config=body.risk_config,
        enabled=body.enabled,
        tenant_id=tenant_id,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


@router.put("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(agent, key, value)

    agent.version += 1
    await db.commit()
    await db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")

    # Delete all agent-skill bindings for this agent
    await db.execute(delete(AgentSkill).where(AgentSkill.agent_id == agent_id))

    # Delete auto-managed skills
    tag = f"agent:{agent_id}"
    result = await db.execute(select(Skill).where(Skill.managed_by == tag))
    for skill in result.scalars().all():
        await db.delete(skill)

    await db.delete(agent)
    await db.commit()


@router.post("/bulk-update")
async def bulk_update_agents(
    body: dict, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Bulk enable/disable agents."""
    agent_ids = body.get("agent_ids", [])
    updates = body.get("updates", {})
    if not agent_ids or not updates:
        raise HTTPException(status_code=400, detail="agent_ids and updates required")

    # Only allow safe fields to be bulk-updated
    ALLOWED_FIELDS = {"enabled", "description", "llm_model", "llm_config_id"}
    safe_updates = {k: v for k, v in updates.items() if k in ALLOWED_FIELDS}
    if not safe_updates:
        raise HTTPException(status_code=400, detail=f"No valid fields. Allowed: {sorted(ALLOWED_FIELDS)}")

    count = 0
    for aid in agent_ids:
        result = await db.execute(
            select(Agent).where(Agent.id == aid, Agent.tenant_id == tenant_id)
        )
        agent = result.scalar_one_or_none()
        if agent:
            for key, value in safe_updates.items():
                setattr(agent, key, value)
            count += 1
    await db.commit()
    return {"updated": count}


@router.get("/{agent_id}/export")
async def export_agent(
    agent_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Export agent configuration as JSON."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get associated skills
    skills_result = await db.execute(
        select(Skill).join(AgentSkill, AgentSkill.skill_id == Skill.id).where(AgentSkill.agent_id == agent_id)
    )
    skills = skills_result.scalars().all()

    export_data = {
        "name": agent.name,
        "description": agent.description,
        "system_prompt": agent.system_prompt,
        "llm_model": agent.llm_model,
        "response_config": agent.response_config,
        "risk_config": agent.risk_config,
        "skill_routing_mode": agent.skill_routing_mode,
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "skill_type": s.skill_type,
                "execution_config": s.execution_config,
                "priority": s.priority,
            }
            for s in skills
        ],
        "export_version": "1.0",
    }
    return export_data


@router.post("/import")
async def import_agent(
    body: dict, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Import agent configuration from exported JSON."""
    name = body.get("name", "Imported Agent")
    if not name or not isinstance(name, str):
        raise HTTPException(status_code=400, detail="name is required and must be a string")

    # Validate skill types if present
    valid_skill_types = {"knowledge_qa", "workflow", "tool_call", "delegate", "chitchat", "composite"}
    for skill_data in body.get("skills", []):
        st = skill_data.get("skill_type")
        if st and st not in valid_skill_types:
            raise HTTPException(status_code=400, detail=f"Invalid skill_type: {st}")

    # Check for duplicate name (tenant from the authenticated caller — never trust the body)
    existing = await db.execute(
        select(Agent).where(Agent.name == name, Agent.tenant_id == tenant_id)
    )
    if existing.scalar_one_or_none():
        name = f"{name} (imported)"

    agent = Agent(
        name=name,
        description=body.get("description", ""),
        system_prompt=body.get("system_prompt", ""),
        llm_model=body.get("llm_model"),
        response_config=body.get("response_config"),
        risk_config=body.get("risk_config"),
        skill_routing_mode=body.get("skill_routing_mode", "conversational"),
        tenant_id=tenant_id,
    )
    db.add(agent)
    await db.flush()

    # Import skills
    for skill_data in body.get("skills", []):
        skill = Skill(
            name=f"{skill_data.get('name', 'imported')}_{agent.id[:8]}",
            description=skill_data.get("description", ""),
            skill_type=skill_data.get("skill_type", "knowledge_qa"),
            execution_config=skill_data.get("execution_config"),
            priority=skill_data.get("priority", 0),
            managed_by=f"agent:{agent.id}",
            tenant_id=agent.tenant_id,
        )
        db.add(skill)
        await db.flush()
        db.add(AgentSkill(agent_id=agent.id, skill_id=skill.id))

    await db.commit()
    return {"id": agent.id, "name": agent.name}


@router.post("/{agent_id}/clone")
async def clone_agent(
    agent_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Clone an agent with all its skills."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Agent not found")

    clone = Agent(
        name=f"{original.name} (copy)",
        description=original.description,
        system_prompt=original.system_prompt,
        llm_model=original.llm_model,
        llm_config_id=original.llm_config_id,
        response_config=original.response_config,
        risk_config=original.risk_config,
        skill_routing_mode=original.skill_routing_mode,
        tenant_id=original.tenant_id,
    )
    db.add(clone)
    await db.flush()

    # Clone managed skills
    skills_result = await db.execute(
        select(Skill).join(AgentSkill, AgentSkill.skill_id == Skill.id).where(AgentSkill.agent_id == agent_id)
    )
    for original_skill in skills_result.scalars().all():
        new_skill = Skill(
            name=f"{original_skill.name}_clone",
            description=original_skill.description,
            skill_type=original_skill.skill_type,
            execution_config=original_skill.execution_config,
            priority=original_skill.priority,
            managed_by=f"agent:{clone.id}",
            tenant_id=clone.tenant_id,
        )
        db.add(new_skill)
        await db.flush()
        db.add(AgentSkill(agent_id=clone.id, skill_id=new_skill.id))

    await db.commit()
    return {"id": clone.id, "name": clone.name}
