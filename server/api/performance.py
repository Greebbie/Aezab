"""Performance presets and runtime configuration API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server.middleware.auth import get_current_user, require_role, require_scope
from server.performance_presets import PRESETS
from server.runtime_config import runtime_config

router = APIRouter(dependencies=[Depends(get_current_user)])


class ApplyPresetRequest(BaseModel):
    preset: str


class UpdateConfigRequest(BaseModel):
    config: dict


@router.get("/presets")
async def list_presets():
    """List all available performance presets."""
    return {name: preset for name, preset in PRESETS.items()}


@router.get("/presets/{preset_name}")
async def get_preset(preset_name: str):
    """Get a single performance preset by name."""
    if preset_name not in PRESETS:
        raise HTTPException(404, f"Preset '{preset_name}' not found")
    return PRESETS[preset_name]


@router.post(
    "/presets/apply",
    dependencies=[Depends(require_scope("manage")), Depends(require_role("admin"))],
)
async def apply_preset(body: ApplyPresetRequest):
    """Apply a performance preset, updating runtime config."""
    if body.preset not in PRESETS:
        raise HTTPException(400, f"Unknown preset '{body.preset}'. Choose from: {list(PRESETS.keys())}")
    preset_data = PRESETS[body.preset]
    runtime_config.update(preset_data)
    runtime_config.set("active_preset", body.preset)
    return {"message": f"Preset '{body.preset}' applied", "config": runtime_config.all()}


@router.get("/current-config")
async def get_current_config():
    """Get the current runtime configuration."""
    return runtime_config.all()


@router.post(
    "/update-config",
    dependencies=[Depends(require_scope("manage")), Depends(require_role("admin"))],
)
async def update_config(body: UpdateConfigRequest):
    """Update runtime config with partial overrides. Clears active preset label."""
    runtime_config.update(body.config)
    runtime_config.set("active_preset", None)
    return {"message": "Config updated", "config": runtime_config.all()}


@router.get("/circuit-breaker/status")
async def circuit_breaker_status():
    """Get status of all circuit breakers."""
    from server.engine.circuit_breaker import circuit_breaker
    return {"circuits": circuit_breaker.get_all_status()}
