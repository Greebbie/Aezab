"""Shared helper: find which agents rely on a knowledge/tool/workflow resource.

Resource-to-agent links are indirect: a resource id (knowledge source id,
tool id, workflow id) is embedded inside a Skill's `execution_config`, and
the Skill is bound to an Agent via AgentSkill (and/or tagged
`managed_by="agent:{id}"` for auto-managed skills — see
server/api/agent_capabilities.py for the execution_config shapes:
  - knowledge_qa: execution_config["knowledge_source_ids"] (list[str])
  - workflow:     execution_config["workflow_id"] (str)
  - tool_call:    execution_config["tool_ids"] (list[str])

Used by the "delete this, but N agents use it" confirmation flow in
knowledge.py / tools.py / workflows.py.
"""

from __future__ import annotations

from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.agent import Agent
from server.models.agent_skill import AgentSkill
from server.models.skill import Skill


async def get_resource_usage(
    db: AsyncSession,
    tenant_id: str,
    *,
    skill_type: str,
    matches: Callable[[dict], bool],
) -> dict:
    """Return `{"used_by": [{"agent_id", "agent_name"}], "count": N}`.

    Scans skills of `skill_type` within the tenant, keeps the ones whose
    execution_config satisfies `matches`, then resolves the owning agents
    through AgentSkill bindings (with a managed_by fallback for auto-managed
    skills whose binding row may be missing).
    """
    result = await db.execute(
        select(Skill).where(Skill.skill_type == skill_type, Skill.tenant_id == tenant_id)
    )
    matching_skills = [s for s in result.scalars().all() if matches(s.execution_config or {})]
    if not matching_skills:
        return {"used_by": [], "count": 0}

    matching_ids = [s.id for s in matching_skills]
    binding_result = await db.execute(
        select(AgentSkill.agent_id).where(AgentSkill.skill_id.in_(matching_ids))
    )
    agent_ids: set[str] = {row[0] for row in binding_result.all()}

    # Fallback for auto-managed skills whose AgentSkill row is somehow missing.
    for skill in matching_skills:
        if skill.managed_by and skill.managed_by.startswith("agent:"):
            agent_ids.add(skill.managed_by.split(":", 1)[1])

    if not agent_ids:
        return {"used_by": [], "count": 0}

    agents_result = await db.execute(
        select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids), Agent.tenant_id == tenant_id)
    )
    used_by = [{"agent_id": row[0], "agent_name": row[1]} for row in agents_result.all()]
    used_by.sort(key=lambda item: item["agent_name"])
    return {"used_by": used_by, "count": len(used_by)}
