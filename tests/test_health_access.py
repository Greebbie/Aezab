"""Readiness stays public; triggering billed provider calls is privileged."""
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

import server.main as main
import server.engine.llm_health as llm_health


@pytest.mark.asyncio
async def test_basic_health_does_not_authenticate_or_probe_model(monkeypatch):
    authenticate = AsyncMock(side_effect=HTTPException(401, "Authentication required"))
    probe = AsyncMock(return_value={"status": "healthy"})
    monkeypatch.setattr(main, "get_current_user", authenticate)
    monkeypatch.setattr(llm_health, "check_llm_health", probe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.get("/health?force=true")
    assert response.status_code == 200
    authenticate.assert_not_awaited()
    probe.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("identity,status", [
    (None, 401),
    ({"role": "viewer"}, 403),
    ({"role": "editor"}, 403),
    ({"role": "admin", "api_key_scopes": ["invoke"]}, 403),
    ({"role": "admin", "api_key_scopes": ["manage"]}, 200),
    ({"role": "admin"}, 200),
])
async def test_paid_health_probe_checks_scope_and_role(monkeypatch, identity, status):
    authenticate = AsyncMock(return_value=identity)
    if identity is None:
        authenticate.side_effect = HTTPException(401, "Authentication required")
    probe = AsyncMock(return_value={"status": "healthy"})
    monkeypatch.setattr(main, "get_current_user", authenticate)
    monkeypatch.setattr(llm_health, "check_llm_health", probe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.get("/health?check_llm=true&force=true")
    assert response.status_code == status
    if status == 200:
        probe.assert_awaited_once_with(force=True)
    else:
        probe.assert_not_awaited()
