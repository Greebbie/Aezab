"""Schemas for Workflow CRUD."""

from __future__ import annotations

from typing import Any
from datetime import datetime

from pydantic import BaseModel


class FieldDef(BaseModel):
    name: str
    label: str
    field_type: str = "text"  # text|number|date|phone|id_card|file|select|multi_select|address|custom
    required: bool = True
    validation_rule: str | None = None  # regex or built-in rule name
    placeholder: str = ""
    options: list[dict[str, Any]] | None = None  # for select/multi_select
    # File upload config (only for field_type="file")
    file_config: dict[str, Any] | None = None  # {max_size_mb, allowed_extensions, storage_path}
    # LLM-assisted validation (semantic checks like "is this a valid address?")
    llm_validate: bool = False
    llm_validate_prompt: str | None = None


class StepCreate(BaseModel):
    name: str
    order: int
    step_type: str = "collect"
    prompt_template: str = ""
    fields: list[FieldDef] | None = None
    validation_rules: dict[str, Any] | None = None
    tool_id: str | None = None
    tool_config: dict[str, Any] | None = None
    on_failure: str = "retry"
    max_retries: int = 2
    fallback_step_id: str | None = None
    requires_human_confirm: bool = False
    risk_level: str = "info"
    next_step_rules: dict[str, Any] | list[dict[str, Any]] | None = None


class StepOut(BaseModel):
    id: str
    workflow_id: str
    name: str
    order: int
    step_type: str
    prompt_template: str
    fields: Any
    validation_rules: Any
    tool_id: str | None
    tool_config: Any
    on_failure: str
    max_retries: int
    fallback_step_id: str | None
    requires_human_confirm: bool
    risk_level: str
    next_step_rules: Any
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkflowCreate(BaseModel):
    name: str
    description: str = ""
    tenant_id: str = "default"
    config: dict[str, Any] | None = None
    steps: list[StepCreate] | None = None


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict[str, Any] | None = None


class WorkflowOut(BaseModel):
    id: str
    name: str
    description: str
    tenant_id: str
    version: int
    config: Any
    steps: list[StepOut] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
