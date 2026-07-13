"""LLM configuration CRUD API."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.engine.secrets_store import encrypt_secret
from server.middleware.auth import get_current_user, get_tenant_id
from server.models.llm_config import LLMConfig

logger = logging.getLogger(__name__)

# Placeholder the API returns in place of a real key (see LLMConfigOut.api_key
# below) and that the frontend echoes back unchanged when the user didn't
# touch the key field. `update_config` treats this sentinel as "no change".
_MASKED_API_KEY = "********"

router = APIRouter(dependencies=[Depends(get_current_user)])

# ── Pydantic Schemas ────────────────────────────────


class LLMConfigCreate(BaseModel):
    name: str
    provider: str  # openai_compatible | dashscope | zhipu | local
    base_url: str
    api_key: str = ""
    model: str
    temperature: float = 0.3
    top_p: float = 1.0
    max_tokens: int = 2048
    timeout_ms: int = 60000
    extra_params: dict[str, Any] | None = None
    is_default: bool = False
    tenant_id: str = "default"


class LLMConfigUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    timeout_ms: int | None = None
    extra_params: dict[str, Any] | None = None
    is_default: bool | None = None


class LLMConfigOut(BaseModel):
    id: str
    name: str
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    top_p: float
    max_tokens: int
    timeout_ms: int
    extra_params: Any
    is_default: bool
    tenant_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("api_key", mode="before")
    @classmethod
    def _mask_api_key(cls, v: str) -> str:
        """Never echo the stored key (plaintext or ciphertext) back to the
        client -- a non-empty key becomes a fixed placeholder, an unset key
        stays empty. The frontend treats an unchanged placeholder as "leave
        the key alone" (see `update_config` below)."""
        return _MASKED_API_KEY if v else ""


class LLMConfigTestRequest(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    temperature: float = 0.3
    max_tokens: int = 256
    timeout_ms: int = 30000


class LLMConfigTestResponse(BaseModel):
    success: bool
    content: str = ""
    latency_ms: float = 0.0
    error: str | None = None


# ── Provider Templates ──────────────────────────────

PROVIDER_TEMPLATES: dict[str, dict[str, Any]] = {
    "openai_compatible": {
        "provider": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "temperature": 0.3,
        "top_p": 1.0,
        "max_tokens": 2048,
        "timeout_ms": 60000,
    },
    "dashscope": {
        "provider": "dashscope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-flash",
        "temperature": 0.3,
        "top_p": 0.8,
        "max_tokens": 2048,
        "timeout_ms": 60000,
    },
    "zhipu": {
        "provider": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4",
        "temperature": 0.3,
        "top_p": 0.7,
        "max_tokens": 2048,
        "timeout_ms": 60000,
    },
    "ollama": {
        "provider": "local",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:1.5b",
        "temperature": 0.3,
        "top_p": 1.0,
        "max_tokens": 2048,
        "timeout_ms": 120000,
    },
    "vllm": {
        "provider": "openai_compatible",
        "base_url": "http://localhost:8080/v1",
        "model": "Qwen/Qwen2.5-72B-Instruct",
        "temperature": 0.3,
        "top_p": 0.95,
        "max_tokens": 4096,
        "timeout_ms": 120000,
    },
    "minimax": {
        "provider": "openai_compatible",
        "base_url": "https://api.minimaxi.com/v1",
        "model": "MiniMax-M2",
        "temperature": 0.3,
        "top_p": 1.0,
        "max_tokens": 2048,
        "timeout_ms": 60000,
    },
}

# ── Endpoints ───────────────────────────────────────


@router.get("/templates")
async def get_templates():
    """Return provider templates with default values."""
    return PROVIDER_TEMPLATES


@router.get("/", response_model=list[LLMConfigOut])
async def list_configs(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LLMConfig).where(LLMConfig.tenant_id == tenant_id))
    return result.scalars().all()


@router.post("/", response_model=LLMConfigOut, status_code=201)
async def create_config(
    body: LLMConfigCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    config = LLMConfig(
        name=body.name,
        provider=body.provider,
        base_url=body.base_url,
        api_key=encrypt_secret(body.api_key),
        model=body.model,
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_tokens,
        timeout_ms=body.timeout_ms,
        extra_params=body.extra_params,
        is_default=body.is_default,
        tenant_id=tenant_id,
    )
    # If this config is set as default, unset any existing defaults for the tenant
    if body.is_default:
        existing = await db.execute(
            select(LLMConfig).where(
                LLMConfig.tenant_id == tenant_id,
                LLMConfig.is_default == True,  # noqa: E712
            )
        )
        for old in existing.scalars().all():
            old.is_default = False

    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


@router.get("/{config_id}", response_model=LLMConfigOut)
async def get_config(
    config_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LLMConfig).where(LLMConfig.id == config_id, LLMConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "LLM config not found")
    return config


@router.put("/{config_id}", response_model=LLMConfigOut)
async def update_config(
    config_id: str,
    body: LLMConfigUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LLMConfig).where(LLMConfig.id == config_id, LLMConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "LLM config not found")

    update_data = body.model_dump(exclude_unset=True)

    # The frontend receives the masked placeholder in list/get responses and
    # echoes it back unchanged when the user didn't edit the key field --
    # treat that as "no change" rather than overwriting the real key with
    # the literal placeholder string. A genuinely new (or explicitly
    # cleared, i.e. "") key gets encrypted before it touches the DB.
    if "api_key" in update_data:
        if update_data["api_key"] == _MASKED_API_KEY:
            del update_data["api_key"]
        else:
            update_data["api_key"] = encrypt_secret(update_data["api_key"])

    # If setting this config as default, unset others first
    if update_data.get("is_default"):
        existing = await db.execute(
            select(LLMConfig).where(
                LLMConfig.tenant_id == config.tenant_id,
                LLMConfig.is_default == True,  # noqa: E712
                LLMConfig.id != config_id,
            )
        )
        for old in existing.scalars().all():
            old.is_default = False

    for key, value in update_data.items():
        setattr(config, key, value)

    await db.commit()
    await db.refresh(config)
    return config


@router.delete("/{config_id}", status_code=204)
async def delete_config(
    config_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LLMConfig).where(LLMConfig.id == config_id, LLMConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "LLM config not found")
    await db.delete(config)
    await db.commit()


@router.post("/set-default/{config_id}", response_model=LLMConfigOut)
async def set_default(
    config_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Set a config as the default, unsetting any existing default for the same tenant."""
    result = await db.execute(
        select(LLMConfig).where(LLMConfig.id == config_id, LLMConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "LLM config not found")

    # Unset existing defaults for this tenant
    existing = await db.execute(
        select(LLMConfig).where(
            LLMConfig.tenant_id == config.tenant_id,
            LLMConfig.is_default == True,  # noqa: E712
        )
    )
    for old in existing.scalars().all():
        old.is_default = False

    config.is_default = True
    await db.commit()
    await db.refresh(config)
    return config


@router.post("/test", response_model=LLMConfigTestResponse)
async def test_config(body: LLMConfigTestRequest):
    """Test an LLM config by sending a simple prompt."""
    from server.engine.llm_adapter import LLMAdapter, LLMMessage

    adapter = LLMAdapter(
        base_url=body.base_url,
        api_key=body.api_key,
        model=body.model,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        timeout=body.timeout_ms // 1000,
    )
    try:
        resp = await adapter.chat([
            LLMMessage(role="user", content="Say hello in one sentence."),
        ])
        return LLMConfigTestResponse(
            success=True,
            content=resp.content,
            latency_ms=resp.latency_ms,
        )
    except Exception as e:
        logger.warning(f"LLM config test failed: {e}")
        return LLMConfigTestResponse(
            success=False,
            error=str(e),
        )
