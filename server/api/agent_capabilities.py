"""Agent Capabilities API — auto-manage skills from agent page."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.models.agent import Agent
from server.models.skill import Skill
from server.models.agent_skill import AgentSkill
from server.middleware.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


# ── Schemas ──────────────────────────────────────────

class KnowledgeCapability(BaseModel):
    domain: str = "default"
    source_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)  # optional, unused in conversational mode
    description: str = ""  # used as tool description in conversational mode


class WorkflowCapability(BaseModel):
    workflow_id: str
    keywords: list[str] = Field(default_factory=list)  # optional, unused in conversational mode
    description: str = ""  # used as tool description in conversational mode


class ToolCapability(BaseModel):
    tool_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)  # optional, unused in conversational mode
    description: str = ""  # used as tool description in conversational mode


class CapabilitiesPayload(BaseModel):
    knowledge: list[KnowledgeCapability] = Field(default_factory=list)
    workflows: list[WorkflowCapability] = Field(default_factory=list)
    tools: list[ToolCapability] = Field(default_factory=list)


class CapabilitiesResponse(BaseModel):
    knowledge: list[dict[str, Any]] = Field(default_factory=list)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)


# ── Helpers ──────────────────────────────────────────

def _managed_tag(agent_id: str) -> str:
    return f"agent:{agent_id}"


def _skill_to_capability(skill: Skill) -> tuple[str, dict]:
    """Convert a managed Skill back into a capability dict.

    Returns (capability_type, data_dict).
    """
    ec = skill.execution_config or {}
    tc = skill.trigger_config or {}

    if skill.skill_type == "knowledge_qa":
        return "knowledge", {
            "domain": ec.get("domain", "default"),
            "source_ids": ec.get("knowledge_source_ids", []),
            "keywords": tc.get("keywords", []),
            "description": skill.description or "",
        }
    elif skill.skill_type == "workflow":
        return "workflows", {
            "workflow_id": ec.get("workflow_id", ""),
            "keywords": tc.get("keywords", []),
            "description": tc.get("trigger_description", ""),
        }
    elif skill.skill_type == "tool_call":
        return "tools", {
            "tool_ids": ec.get("tool_ids", []),
            "keywords": tc.get("keywords", []),
            "description": tc.get("trigger_description", ""),
        }
    return "unknown", {}


# ── GET capabilities ─────────────────────────────────

@router.get("/{agent_id}/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(agent_id: str, db: AsyncSession = Depends(get_db)):
    # Verify agent exists
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Agent not found")

    # Load managed skills
    tag = _managed_tag(agent_id)
    result = await db.execute(select(Skill).where(Skill.managed_by == tag))
    skills = result.scalars().all()

    caps: dict[str, list] = {"knowledge": [], "workflows": [], "tools": []}
    for skill in skills:
        cap_type, data = _skill_to_capability(skill)
        if cap_type in caps:
            caps[cap_type].append(data)

    return caps


# ── PUT capabilities ─────────────────────────────────

@router.put("/{agent_id}/capabilities", response_model=CapabilitiesResponse)
async def update_capabilities(
    agent_id: str,
    body: CapabilitiesPayload,
    db: AsyncSession = Depends(get_db),
):
    # Verify agent exists and get name for auto-generated skill names
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")

    tag = _managed_tag(agent_id)

    # Load existing managed skills
    result = await db.execute(select(Skill).where(Skill.managed_by == tag))
    existing_skills = list(result.scalars().all())

    # Index existing skills by (skill_type, unique_key) for reconciliation
    existing_map: dict[tuple[str, str], Skill] = {}
    for sk in existing_skills:
        ec = sk.execution_config or {}
        if sk.skill_type == "knowledge_qa":
            key = ("knowledge_qa", ec.get("domain", "default"))
        elif sk.skill_type == "workflow":
            key = ("workflow", ec.get("workflow_id", ""))
        elif sk.skill_type == "tool_call":
            # Use sorted tool_ids as key to identify the same tool binding
            key = ("tool_call", ",".join(sorted(ec.get("tool_ids", []))))
        else:
            key = (sk.skill_type, sk.id)
        existing_map[key] = sk

    matched_skill_ids: set[str] = set()

    # ── Process knowledge capabilities ──
    for cap in body.knowledge:
        key = ("knowledge_qa", cap.domain)
        execution_config = {
            "knowledge_source_ids": cap.source_ids,
            "domain": cap.domain,
        }
        trigger_config = {"keywords": cap.keywords} if cap.keywords else None
        skill_desc = cap.description or f"搜索知识库 ({cap.domain}) 获取相关信息"

        if key in existing_map:
            skill = existing_map[key]
            skill.execution_config = execution_config
            skill.trigger_config = trigger_config
            skill.description = skill_desc
            matched_skill_ids.add(skill.id)
        else:
            skill = Skill(
                name=f"[auto] {agent.name} - 知识问答 ({cap.domain})",
                description=skill_desc,
                skill_type="knowledge_qa",
                execution_config=execution_config,
                trigger_config=trigger_config,
                managed_by=tag,
                tenant_id=agent.tenant_id,
            )
            db.add(skill)
            await db.flush()
            db.add(AgentSkill(agent_id=agent_id, skill_id=skill.id))
            matched_skill_ids.add(skill.id)

    # ── Process workflow capabilities ──
    for cap in body.workflows:
        key = ("workflow", cap.workflow_id)
        execution_config = {"workflow_id": cap.workflow_id}
        trigger_config: dict[str, Any] = {}
        if cap.keywords:
            trigger_config["keywords"] = cap.keywords
        if cap.description:
            trigger_config["trigger_description"] = cap.description

        if key in existing_map:
            skill = existing_map[key]
            skill.execution_config = execution_config
            skill.trigger_config = trigger_config or None
            matched_skill_ids.add(skill.id)
        else:
            skill = Skill(
                name=f"[auto] {agent.name} - 工作流",
                description=cap.description or "Auto-managed workflow skill",
                skill_type="workflow",
                execution_config=execution_config,
                trigger_config=trigger_config or None,
                managed_by=tag,
                tenant_id=agent.tenant_id,
            )
            db.add(skill)
            await db.flush()
            db.add(AgentSkill(agent_id=agent_id, skill_id=skill.id))
            matched_skill_ids.add(skill.id)

    # ── Process tool capabilities ──
    for cap in body.tools:
        key = ("tool_call", ",".join(sorted(cap.tool_ids)))
        execution_config = {
            "tool_ids": cap.tool_ids,
            "function_calling_enabled": True,
            "max_tool_rounds": 5,
        }
        trigger_config_t: dict[str, Any] = {}
        if cap.keywords:
            trigger_config_t["keywords"] = cap.keywords
        if cap.description:
            trigger_config_t["trigger_description"] = cap.description

        if key in existing_map:
            skill = existing_map[key]
            skill.execution_config = execution_config
            skill.trigger_config = trigger_config_t or None
            matched_skill_ids.add(skill.id)
        else:
            skill = Skill(
                name=f"[auto] {agent.name} - 工具调用",
                description=cap.description or "Auto-managed tool calling skill",
                skill_type="tool_call",
                execution_config=execution_config,
                trigger_config=trigger_config_t or None,
                managed_by=tag,
                tenant_id=agent.tenant_id,
            )
            db.add(skill)
            await db.flush()
            db.add(AgentSkill(agent_id=agent_id, skill_id=skill.id))
            matched_skill_ids.add(skill.id)

    # ── Delete unmatched managed skills ──
    for sk in existing_skills:
        if sk.id not in matched_skill_ids:
            # Remove agent-skill bindings first
            await db.execute(
                delete(AgentSkill).where(AgentSkill.skill_id == sk.id)
            )
            await db.delete(sk)

    await db.commit()

    # Return updated capabilities
    result = await db.execute(select(Skill).where(Skill.managed_by == tag))
    updated_skills = result.scalars().all()

    caps: dict[str, list] = {"knowledge": [], "workflows": [], "tools": []}
    for skill in updated_skills:
        cap_type, data = _skill_to_capability(skill)
        if cap_type in caps:
            caps[cap_type].append(data)

    return caps
