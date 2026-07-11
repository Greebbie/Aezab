"""Agent Templates API — 傻瓜式 (dead-simple) template-driven agent stamping.

A customer picks a built-in template (knowledge QA / repair ticket /
booking) and gets a fully working Chinese customer-service agent in one
call — no manual skill wiring. Reuses the existing managed-skill convention
from server/api/agent_capabilities.py (`managed_by=f"agent:{agent_id}"`) so
these agents look and behave exactly like ones configured by hand through
the capabilities API.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.middleware.auth import get_current_user, get_tenant_id
from server.models.agent import Agent
from server.models.agent_skill import AgentSkill
from server.models.skill import Skill
from server.models.workflow import Workflow, WorkflowStep
from server.schemas.agent import AgentOut
from server.templates import get_template, list_templates

router = APIRouter(dependencies=[Depends(get_current_user)])


# ── Schemas ──────────────────────────────────────────

class TemplateOut(BaseModel):
    id: str
    name: str
    description: str
    category: str


class InstantiateRequest(BaseModel):
    name: str | None = None
    llm_config_id: str | None = None


# ── Helpers ──────────────────────────────────────────

def _managed_tag(agent_id: str) -> str:
    return f"agent:{agent_id}"


def _trigger_config(cap: dict[str, Any]) -> dict[str, Any] | None:
    """Store keywords/description hints the same way agent_capabilities.py does."""
    trigger_config: dict[str, Any] = {}
    keywords = cap.get("keywords") or []
    description = (cap.get("description") or "").strip()
    if keywords:
        trigger_config["keywords"] = keywords
    if description:
        trigger_config["trigger_description"] = description
    return trigger_config or None


async def _unique_agent_name(db: AsyncSession, tenant_id: str, requested_name: str) -> str:
    """Avoid the (tenant_id, name) unique constraint when a customer stamps
    several agents from the same template without picking a custom name."""
    result = await db.execute(
        select(Agent.id).where(Agent.tenant_id == tenant_id, Agent.name == requested_name)
    )
    if result.scalar_one_or_none() is None:
        return requested_name
    return f"{requested_name}-{uuid.uuid4().hex[:6]}"


def _add_knowledge_skills(
    db: AsyncSession, agent: Agent, tenant_id: str, tag: str, caps: dict[str, Any],
) -> None:
    """Create + bind one knowledge_qa skill per capability entry (mirrors
    agent_capabilities.py's PUT /capabilities knowledge-skill creation)."""
    for cap in caps.get("knowledge", []):
        domain = cap.get("domain", "default")
        skill = Skill(
            name=f"[auto] {agent.name} - 知识问答 ({domain})",
            description=cap.get("description", ""),
            skill_type="knowledge_qa",
            execution_config={
                "knowledge_source_ids": cap.get("source_ids", []),
                "domain": domain,
            },
            trigger_config=_trigger_config(cap),
            managed_by=tag,
            tenant_id=tenant_id,
        )
        db.add(skill)


async def _create_workflow_with_steps(
    db: AsyncSession, tenant_id: str, workflow_spec: dict[str, Any],
) -> Workflow:
    """Create the Workflow + WorkflowStep rows declared by the template spec.

    Rows are real (not just a description) so the result is immediately
    loadable/runnable by the existing WorkflowExecutor.get_steps().
    """
    workflow = Workflow(
        name=workflow_spec["name"],
        description=workflow_spec.get("description", ""),
        tenant_id=tenant_id,
    )
    db.add(workflow)
    await db.flush()

    for step_spec in workflow_spec["steps"]:
        step = WorkflowStep(
            workflow_id=workflow.id,
            name=step_spec["name"],
            order=step_spec["order"],
            step_type=step_spec.get("step_type", "collect"),
            prompt_template=step_spec.get("prompt_template", ""),
            fields=step_spec.get("fields"),
            validation_rules=step_spec.get("validation_rules"),
            tool_id=step_spec.get("tool_id"),
            tool_config=step_spec.get("tool_config"),
            on_failure=step_spec.get("on_failure", "retry"),
            max_retries=step_spec.get("max_retries", 2),
            requires_human_confirm=step_spec.get("requires_human_confirm", False),
            risk_level=step_spec.get("risk_level", "info"),
        )
        db.add(step)

    await db.flush()
    return workflow


def _add_workflow_skill(
    db: AsyncSession, agent: Agent, tenant_id: str, tag: str, workflow: Workflow, caps: dict[str, Any],
) -> None:
    """Create + bind one workflow skill pointing at the just-created workflow
    (mirrors agent_capabilities.py's workflow-skill creation)."""
    workflow_caps = caps.get("workflows") or [{}]
    for cap in workflow_caps:
        skill = Skill(
            name=f"[auto] {agent.name} - 工作流",
            description=cap.get("description") or f"Start workflow: {workflow.name}",
            skill_type="workflow",
            execution_config={"workflow_id": workflow.id},
            trigger_config=_trigger_config(cap),
            managed_by=tag,
            tenant_id=tenant_id,
        )
        db.add(skill)


async def _bind_managed_skills(db: AsyncSession, agent: Agent, tag: str) -> None:
    """Flush pending Skill inserts, then bind every managed skill for this
    agent via AgentSkill (skills were added but not yet given ids/bindings)."""
    await db.flush()
    result = await db.execute(select(Skill).where(Skill.managed_by == tag))
    for skill in result.scalars().all():
        db.add(AgentSkill(agent_id=agent.id, skill_id=skill.id))


# ── Endpoints ────────────────────────────────────────

@router.get("/", response_model=list[TemplateOut])
async def list_agent_templates():
    """List built-in agent templates. Tenant-agnostic — they're static data."""
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "description": t["description"],
            "category": t["category"],
        }
        for t in list_templates()
    ]


@router.post("/{template_id}/instantiate", response_model=AgentOut, status_code=201)
async def instantiate_agent_template(
    template_id: str,
    body: InstantiateRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Stamp out a working agent (+ workflow + managed skills) from a template.

    Creates: an Agent (stamped with the caller's tenant), the template's
    workflow (+ steps) if it declares one, and the managed skills binding
    them to the agent — the same shapes agent_capabilities.py would create
    if a human wired this up by hand.
    """
    template = get_template(template_id)
    if template is None:
        raise HTTPException(404, "Template not found")

    requested_name = body.name or template["name"]
    name = await _unique_agent_name(db, tenant_id, requested_name)

    agent = Agent(
        name=name,
        description=template["description"],
        system_prompt=template["system_prompt"],
        llm_config_id=body.llm_config_id,
        response_config=template["response_config"],
        risk_config=template["risk_config"],
        tenant_id=tenant_id,
    )
    db.add(agent)
    await db.flush()

    tag = _managed_tag(agent.id)
    caps = template.get("capabilities", {})

    _add_knowledge_skills(db, agent, tenant_id, tag, caps)

    workflow_spec = template.get("workflow")
    if workflow_spec:
        workflow = await _create_workflow_with_steps(db, tenant_id, workflow_spec)
        _add_workflow_skill(db, agent, tenant_id, tag, workflow, caps)

    await _bind_managed_skills(db, agent, tag)

    await db.commit()
    await db.refresh(agent)
    return agent
