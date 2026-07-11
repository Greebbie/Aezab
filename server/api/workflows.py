"""Workflow management CRUD API."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.db import get_db
from server.middleware.auth import get_current_user, get_tenant_id
from server.models.workflow import Workflow, WorkflowStep
from server.schemas.workflow import WorkflowCreate, WorkflowUpdate, WorkflowOut, StepCreate, StepOut
from server.api._usage_check import get_resource_usage


async def _load_workflow(
    db: AsyncSession, workflow_id: str, tenant_id: str | None = None,
) -> Workflow | None:
    """Load a workflow with steps eagerly loaded (avoids async lazy-load issues).

    When tenant_id is given, the workflow must belong to that tenant or None
    is returned (same 404 behavior as "not found" — no ownership info leak).
    """
    stmt = select(Workflow).where(Workflow.id == workflow_id).options(selectinload(Workflow.steps))
    if tenant_id is not None:
        stmt = stmt.where(Workflow.tenant_id == tenant_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


_ALLOWED_STEP_TYPES = {"collect", "validate", "tool_call", "confirm", "human_review", "complete"}
_ALLOWED_FAILURE_ACTIONS = {"retry", "skip", "rollback", "escalate"}
_ALLOWED_RISK_LEVELS = {"info", "warning", "critical"}
_ALLOWED_FIELD_TYPES = {
    "text",
    "number",
    "date",
    "phone",
    "id_card",
    "email",
    "file",
    "select",
    "multi_select",
    "address",
    "custom",
}
_ALLOWED_RULE_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "lt",
    "gte",
    "lte",
    "contains",
    "not_contains",
    "regex",
    "in",
    "not_in",
}


def _step_payload(step: Any) -> dict[str, Any]:
    if isinstance(step, dict):
        payload = dict(step)
    elif hasattr(step, "model_dump"):
        payload = step.model_dump()
    else:
        payload = {
            "id": getattr(step, "id", None),
            "name": getattr(step, "name", None),
            "order": getattr(step, "order", None),
            "step_type": getattr(step, "step_type", None),
            "prompt_template": getattr(step, "prompt_template", None),
            "fields": getattr(step, "fields", None),
            "validation_rules": getattr(step, "validation_rules", None),
            "tool_id": getattr(step, "tool_id", None),
            "tool_config": getattr(step, "tool_config", None),
            "on_failure": getattr(step, "on_failure", None),
            "max_retries": getattr(step, "max_retries", None),
            "fallback_step_id": getattr(step, "fallback_step_id", None),
            "requires_human_confirm": getattr(step, "requires_human_confirm", None),
            "risk_level": getattr(step, "risk_level", None),
            "next_step_rules": getattr(step, "next_step_rules", None),
        }
    return payload


def _field_payload(field: Any) -> dict[str, Any]:
    if isinstance(field, dict):
        return field
    if hasattr(field, "model_dump"):
        return field.model_dump()
    return {}


def _validate_step_fields(step_name: str, fields: Any, errors: list[str]) -> None:
    if not fields:
        return
    if not isinstance(fields, list):
        errors.append(f"Step '{step_name}' fields must be a list.")
        return

    field_names: set[str] = set()
    for idx, raw_field in enumerate(fields):
        field = _field_payload(raw_field)
        if not field:
            errors.append(f"Step '{step_name}' field #{idx + 1} is invalid.")
            continue

        field_name = str(field.get("name") or "").strip()
        if not field_name:
            errors.append(f"Step '{step_name}' field #{idx + 1} is missing name.")
        elif field_name in field_names:
            errors.append(f"Step '{step_name}' has duplicate field '{field_name}'.")
        field_names.add(field_name)

        if not str(field.get("label") or "").strip():
            errors.append(f"Step '{step_name}' field '{field_name or idx + 1}' is missing label.")

        field_type = field.get("field_type") or "text"
        if field_type not in _ALLOWED_FIELD_TYPES:
            errors.append(f"Step '{step_name}' field '{field_name}' has invalid type '{field_type}'.")

        if field_type in {"select", "multi_select"}:
            options = field.get("options")
            if not isinstance(options, list) or not options:
                errors.append(f"Step '{step_name}' field '{field_name}' requires non-empty options.")

        if field_type == "file":
            file_config = field.get("file_config") or {}
            if not isinstance(file_config, dict):
                errors.append(f"Step '{step_name}' field '{field_name}' file_config must be an object.")
            else:
                allowed_ext = file_config.get("allowed_extensions")
                if allowed_ext is not None and not isinstance(allowed_ext, list):
                    errors.append(
                        f"Step '{step_name}' field '{field_name}' allowed_extensions must be a list."
                    )
                max_size = file_config.get("max_size_mb")
                if max_size is not None:
                    try:
                        if float(max_size) <= 0:
                            errors.append(
                                f"Step '{step_name}' field '{field_name}' max_size_mb must be positive."
                            )
                    except (TypeError, ValueError):
                        errors.append(
                            f"Step '{step_name}' field '{field_name}' max_size_mb must be numeric."
                        )

        validation_rule = field.get("validation_rule")
        if validation_rule:
            try:
                re.compile(str(validation_rule))
            except re.error as exc:
                errors.append(
                    f"Step '{step_name}' field '{field_name}' validation_rule is invalid: {exc}."
                )

        if field.get("llm_validate") and not field.get("llm_validate_prompt"):
            errors.append(f"Step '{step_name}' field '{field_name}' enables LLM validation without a prompt.")


def _validate_next_step_rules(
    step_name: str,
    rules: Any,
    target_refs: set[str],
    errors: list[str],
    enforce_branch_targets: bool = True,
) -> None:
    if not rules:
        return
    if isinstance(rules, dict):
        rules = rules.get("rules", [])
    if not isinstance(rules, list):
        errors.append(f"Step '{step_name}' next_step_rules must be a list or {{rules: [...]}} object.")
        return

    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"Step '{step_name}' branch rule #{idx + 1} must be an object.")
            continue
        goto_step = rule.get("goto_step")
        if not goto_step:
            errors.append(f"Step '{step_name}' branch rule #{idx + 1} is missing goto_step.")
        elif enforce_branch_targets and str(goto_step) not in target_refs:
            errors.append(
                f"Step '{step_name}' branch rule #{idx + 1} points to unknown step '{goto_step}'."
            )

        condition = rule.get("condition")
        if condition is None:
            continue
        if not isinstance(condition, dict):
            errors.append(f"Step '{step_name}' branch rule #{idx + 1} condition must be an object.")
            continue
        if "operator" in condition and "op" not in condition:
            errors.append(f"Step '{step_name}' branch rule #{idx + 1} uses 'operator'; use 'op'.")
        if not condition.get("field"):
            errors.append(f"Step '{step_name}' branch rule #{idx + 1} condition is missing field.")
        op = condition.get("op", "eq")
        if op not in _ALLOWED_RULE_OPERATORS:
            errors.append(f"Step '{step_name}' branch rule #{idx + 1} has invalid op '{op}'.")


def _validate_workflow_steps(
    steps: list[Any],
    require_steps: bool = False,
    enforce_branch_targets: bool = True,
) -> list[str]:
    errors: list[str] = []
    payloads = [_step_payload(step) for step in steps]
    if require_steps and not payloads:
        errors.append("Workflow must contain at least one step before publish.")
        return errors

    names: set[str] = set()
    orders: set[int] = set()
    target_refs = {
        str(payload.get("name")).strip()
        for payload in payloads
        if str(payload.get("name") or "").strip()
    }
    target_refs.update(
        str(payload.get("order"))
        for payload in payloads
        if payload.get("order") is not None
    )

    for idx, payload in enumerate(payloads):
        step_name = str(payload.get("name") or "").strip() or f"#{idx + 1}"
        if not str(payload.get("name") or "").strip():
            errors.append(f"Step #{idx + 1} is missing name.")
        elif step_name in names:
            errors.append(f"Duplicate step name '{step_name}'.")
        names.add(step_name)

        order = payload.get("order")
        if not isinstance(order, int) or order < 0:
            errors.append(f"Step '{step_name}' order must be a non-negative integer.")
        elif order in orders:
            errors.append(f"Duplicate step order '{order}'.")
        elif isinstance(order, int):
            orders.add(order)

        step_type = payload.get("step_type") or "collect"
        if step_type not in _ALLOWED_STEP_TYPES:
            errors.append(f"Step '{step_name}' has invalid type '{step_type}'.")

        on_failure = payload.get("on_failure") or "retry"
        if on_failure not in _ALLOWED_FAILURE_ACTIONS:
            errors.append(f"Step '{step_name}' has invalid on_failure '{on_failure}'.")

        risk_level = payload.get("risk_level") or "info"
        if risk_level not in _ALLOWED_RISK_LEVELS:
            errors.append(f"Step '{step_name}' has invalid risk_level '{risk_level}'.")

        max_retries = payload.get("max_retries")
        if max_retries is not None and (not isinstance(max_retries, int) or max_retries < 0):
            errors.append(f"Step '{step_name}' max_retries must be a non-negative integer.")

        if step_type == "tool_call" and not payload.get("tool_id"):
            errors.append(f"Step '{step_name}' is tool_call but has no tool_id.")

        _validate_step_fields(step_name, payload.get("fields"), errors)
        _validate_next_step_rules(
            step_name,
            payload.get("next_step_rules"),
            target_refs,
            errors,
            enforce_branch_targets=enforce_branch_targets,
        )

    return errors


def _raise_validation_errors(errors: list[str]) -> None:
    if errors:
        raise HTTPException(
            status_code=400,
            detail="Workflow validation failed: " + "; ".join(errors),
        )

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/", response_model=list[WorkflowOut])
async def list_workflows(
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Workflow).where(Workflow.tenant_id == tenant_id).options(selectinload(Workflow.steps))
    )
    return result.scalars().all()


@router.post("/", response_model=WorkflowOut, status_code=201)
async def create_workflow(
    body: WorkflowCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    if body.steps:
        _raise_validation_errors(_validate_workflow_steps(body.steps))

    wf = Workflow(
        name=body.name,
        description=body.description,
        tenant_id=tenant_id,
        config=body.config,
    )
    db.add(wf)
    await db.flush()

    # Create steps
    if body.steps:
        for step_data in body.steps:
            step = WorkflowStep(
                workflow_id=wf.id,
                name=step_data.name,
                order=step_data.order,
                step_type=step_data.step_type,
                prompt_template=step_data.prompt_template,
                fields=[f.model_dump() for f in step_data.fields] if step_data.fields else None,
                validation_rules=step_data.validation_rules,
                tool_id=step_data.tool_id,
                tool_config=step_data.tool_config,
                on_failure=step_data.on_failure,
                max_retries=step_data.max_retries,
                fallback_step_id=step_data.fallback_step_id,
                requires_human_confirm=step_data.requires_human_confirm,
                risk_level=step_data.risk_level,
                next_step_rules=step_data.next_step_rules,
            )
            db.add(step)

    await db.commit()
    wf = await _load_workflow(db, wf.id)
    return wf


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    wf = await _load_workflow(db, workflow_id, tenant_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


@router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: str,
    body: WorkflowUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id)
    )
    wf = result.scalar_one_or_none()
    if not wf:
        raise HTTPException(404, "Workflow not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(wf, key, value)

    wf.version += 1
    await db.commit()
    wf = await _load_workflow(db, wf.id, tenant_id)
    return wf


@router.get("/{workflow_id}/usage")
async def get_workflow_usage(
    workflow_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Report which agents currently depend on this workflow."""
    wf = await _load_workflow(db, workflow_id, tenant_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return await get_resource_usage(
        db, tenant_id,
        skill_type="workflow",
        matches=lambda ec: ec.get("workflow_id") == workflow_id,
    )


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id)
    )
    wf = result.scalar_one_or_none()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    # Delete steps first
    steps = await db.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == wf.id))
    for step in steps.scalars().all():
        await db.delete(step)
    await db.delete(wf)
    await db.commit()


# ── Step management ──────────────────────────────────────────────

@router.post("/{workflow_id}/steps", response_model=StepOut, status_code=201)
async def add_step(
    workflow_id: str,
    body: StepCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    wf = await _load_workflow(db, workflow_id, tenant_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    _raise_validation_errors(
        _validate_workflow_steps([*wf.steps, body], enforce_branch_targets=False)
    )

    step = WorkflowStep(
        workflow_id=workflow_id,
        name=body.name,
        order=body.order,
        step_type=body.step_type,
        prompt_template=body.prompt_template,
        fields=[f.model_dump() for f in body.fields] if body.fields else None,
        validation_rules=body.validation_rules,
        tool_id=body.tool_id,
        tool_config=body.tool_config,
        on_failure=body.on_failure,
        max_retries=body.max_retries,
        fallback_step_id=body.fallback_step_id,
        requires_human_confirm=body.requires_human_confirm,
        risk_level=body.risk_level,
        next_step_rules=body.next_step_rules,
    )
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return step


@router.put("/{workflow_id}/steps/{step_id}", response_model=StepOut)
async def update_step(
    workflow_id: str,
    step_id: str,
    body: StepCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    wf = await _load_workflow(db, workflow_id, tenant_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")

    step = next((s for s in wf.steps if s.id == step_id), None)
    if not step:
        raise HTTPException(404, "Step not found")
    _raise_validation_errors(
        _validate_workflow_steps(
            [body if s.id == step_id else s for s in wf.steps],
            enforce_branch_targets=False,
        )
    )

    step.name = body.name
    step.order = body.order
    step.step_type = body.step_type
    step.prompt_template = body.prompt_template
    step.fields = [f.model_dump() for f in body.fields] if body.fields else None
    step.validation_rules = body.validation_rules
    step.tool_id = body.tool_id
    step.tool_config = body.tool_config
    step.on_failure = body.on_failure
    step.max_retries = body.max_retries
    step.fallback_step_id = body.fallback_step_id
    step.requires_human_confirm = body.requires_human_confirm
    step.risk_level = body.risk_level
    step.next_step_rules = body.next_step_rules

    await db.commit()
    await db.refresh(step)
    return step


@router.delete("/{workflow_id}/steps/{step_id}", status_code=204)
async def delete_step(
    workflow_id: str,
    step_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    wf = await _load_workflow(db, workflow_id, tenant_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    result = await db.execute(
        select(WorkflowStep).where(WorkflowStep.id == step_id, WorkflowStep.workflow_id == workflow_id)
    )
    step = result.scalar_one_or_none()
    if not step:
        raise HTTPException(404, "Step not found")
    await db.delete(step)
    await db.commit()


# ── Version management ──────────────────────────────────────────


@router.post("/{workflow_id}/publish")
async def publish_version(
    workflow_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Publish current workflow state as a new version snapshot."""
    from server.models.workflow import WorkflowVersion

    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id)
    )
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Get current steps
    steps_result = await db.execute(
        select(WorkflowStep)
        .where(WorkflowStep.workflow_id == workflow_id)
        .order_by(WorkflowStep.order)
    )
    steps = list(steps_result.scalars().all())
    _raise_validation_errors(_validate_workflow_steps(steps, require_steps=True))

    # Build snapshot
    snapshot = {
        "name": workflow.name,
        "description": workflow.description,
        "config": workflow.config,
        "steps": [
            {
                "name": s.name,
                "order": s.order,
                "step_type": s.step_type,
                "prompt_template": s.prompt_template,
                "fields": s.fields,
                "validation_rules": s.validation_rules,
                "tool_id": s.tool_id,
                "tool_config": s.tool_config,
                "on_failure": s.on_failure,
                "max_retries": s.max_retries,
                "next_step_rules": s.next_step_rules,
                "requires_human_confirm": s.requires_human_confirm,
                "risk_level": s.risk_level,
            }
            for s in steps
        ],
    }

    version = WorkflowVersion(
        workflow_id=workflow_id,
        version=workflow.version,
        snapshot=snapshot,
    )
    db.add(version)

    # Increment workflow version
    workflow.version += 1
    await db.commit()

    return {"workflow_id": workflow_id, "version": version.version, "id": version.id}


@router.get("/{workflow_id}/versions")
async def list_versions(
    workflow_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """List all published versions of a workflow."""
    from server.models.workflow import WorkflowVersion

    wf = await _load_workflow(db, workflow_id, tenant_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")

    result = await db.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.version.desc())
    )
    versions = result.scalars().all()
    return [
        {
            "id": v.id,
            "workflow_id": v.workflow_id,
            "version": v.version,
            "published_by": v.published_by,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in versions
    ]


@router.get("/{workflow_id}/versions/{version_num}")
async def get_version(
    workflow_id: str,
    version_num: int,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific version snapshot."""
    from server.models.workflow import WorkflowVersion

    wf = await _load_workflow(db, workflow_id, tenant_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")

    result = await db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.version == version_num,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return {
        "id": version.id,
        "workflow_id": version.workflow_id,
        "version": version.version,
        "snapshot": version.snapshot,
        "published_by": version.published_by,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }
