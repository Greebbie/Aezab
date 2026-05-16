"""Schemas for speech-to-text endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from server.engine.asr import ASRProvider


class ASRStatusResponse(BaseModel):
    enabled: bool
    provider: str
    base_url: str
    model: str
    max_file_mb: int
    has_api_key: bool = False
    uses_llm_api_key: bool = False


class ASRConfigResponse(ASRStatusResponse):
    timeout: int
    funasr_path: str
    config_path: str
    config_file_exists: bool = False
    has_saved_config: bool = False
    has_saved_api_key: bool = False
    api_key_source: str = "missing"


class ASRConfigUpdate(BaseModel):
    provider: ASRProvider
    base_url: str = Field(default="", max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    clear_api_key: bool = False
    model: str = Field(default="", max_length=200)
    timeout: int = Field(default=60, ge=5, le=300)
    max_file_mb: int = Field(default=10, ge=1, le=200)
    funasr_path: str = Field(default="/transcribe", max_length=200)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        return value.strip()

    @field_validator("funasr_path")
    @classmethod
    def validate_funasr_path(cls, value: str) -> str:
        value = value.strip() or "/transcribe"
        return value if value.startswith("/") else f"/{value}"


class ASRTranscriptionResponse(BaseModel):
    text: str
    provider: str
    model: str
    language: str | None = None
    duration_seconds: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
