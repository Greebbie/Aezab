"""Skill management API — CRUD for skill definitions."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.models.skill import Skill
from server.schemas.skill import SkillCreate, SkillUpdate, SkillOut
from server.middleware.auth import get_current_user, get_tenant_id

router = APIRouter(dependencies=[Depends(get_current_user)])

VALID_SKILL_TYPES = {"workflow", "tool_call", "knowledge_qa", "delegate", "composite"}


@router.get("/", response_model=list[SkillOut])
async def list_skills(
    tenant_id: str = Depends(get_tenant_id),
    managed_by: Optional[str] = Query(None, description="Filter: 'null' for manual only, or specific tag"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Skill).where(Skill.tenant_id == tenant_id)
    if managed_by == "null":
        stmt = stmt.where(Skill.managed_by.is_(None))
    elif managed_by is not None:
        stmt = stmt.where(Skill.managed_by == managed_by)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=SkillOut, status_code=201)
async def create_skill(
    body: SkillCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    if body.skill_type not in VALID_SKILL_TYPES:
        raise HTTPException(400, f"Invalid skill_type. Must be one of: {VALID_SKILL_TYPES}")

    skill = Skill(
        name=body.name,
        description=body.description,
        skill_type=body.skill_type,
        trigger_config=body.trigger_config,
        execution_config=body.execution_config,
        input_schema=body.input_schema,
        output_schema=body.output_schema,
        priority=body.priority,
        enabled=body.enabled,
        tenant_id=tenant_id,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


@router.get("/{skill_id}", response_model=SkillOut)
async def get_skill(
    skill_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Skill).where(Skill.id == skill_id, Skill.tenant_id == tenant_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "Skill not found")
    return skill


@router.put("/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: str,
    body: SkillUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Skill).where(Skill.id == skill_id, Skill.tenant_id == tenant_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "Skill not found")

    update_data = body.model_dump(exclude_unset=True)
    if "skill_type" in update_data and update_data["skill_type"] not in VALID_SKILL_TYPES:
        raise HTTPException(400, f"Invalid skill_type. Must be one of: {VALID_SKILL_TYPES}")

    for key, value in update_data.items():
        setattr(skill, key, value)

    skill.version = (skill.version or 1) + 1
    await db.commit()
    await db.refresh(skill)
    return skill


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Skill).where(Skill.id == skill_id, Skill.tenant_id == tenant_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "Skill not found")
    await db.delete(skill)
    await db.commit()
