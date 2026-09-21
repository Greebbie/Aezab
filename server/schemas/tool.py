"""Schemas for Tool Gateway management."""

from __future__ import annotations

from typing import Any
from datetime import datetime

from pydantic import BaseModel, field_validator


class ToolCreate(BaseModel):
    name: str
    description: str = ""
    category: str = "api"
    endpoint: str = ""
    method: str = "POST"
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    auth_config: dict[str, Any] | None = None
    timeout_ms: int = 30000
    max_retries: int = 2
    retry_backoff_ms: int = 1000
    is_async: bool = False
    callback_url: str | None = None
    required_permission: str | None = None
    risk_level: str = "info"
    tenant_id: str = "default"


class ToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    endpoint: str | None = None
    method: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    auth_config: dict[str, Any] | None = None
    timeout_ms: int | None = None
    max_retries: int | None = None
    is_async: bool | None = None
    enabled: bool | None = None


class ToolOut(BaseModel):
    id: str
    name: str
    description: str
    category: str
    endpoint: str | None
    method: str
    input_schema: Any
    output_schema: Any
    auth_config: Any
    timeout_ms: int
    max_retries: int
    retry_backoff_ms: int
    is_async: bool
    callback_url: str | None
    required_permission: str | None
    risk_level: str
    tenant_id: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("auth_config", mode="before")
    @classmethod
    def hide_credentials(cls, value):
        if not isinstance(value, dict):
            return None
        return {
            "type": value.get("type", "none"),
            "header": value.get("header", "X-API-Key"),
            "has_token": bool(value.get("token")),
        }


class ToolTestRequest(BaseModel):
    """Test a tool's connectivity."""
    tool_id: str
    test_input: dict[str, Any] | None = None


class ToolTestResponse(BaseModel):
    tool_id: str
    success: bool
    response: Any = None
    error: str | None = None
    latency_ms: float = 0.0
