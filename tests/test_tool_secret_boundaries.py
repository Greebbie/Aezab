"""Tool credentials are write-only and managed queries cannot be redirected."""
from __future__ import annotations

import uuid

import httpx
import pytest

from server.db import async_session
from server.engine.secrets_store import decrypt_secret, migrate_plaintext_tool_tokens
from server.engine.tool_gateway import ToolGateway
from server.main import app
from server.models.tool import ToolDefinition


async def test_credentials_are_encrypted_masked_preserved_rotated_and_cleared():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/tools/", json={
            "name": "secret-test-" + uuid.uuid4().hex,
            "auth_config": {"type": "bearer", "token": "offline-original-token"},
        })
        assert created.status_code == 201
        tool_id = created.json()["id"]
        assert "offline-original-token" not in created.text
        assert created.json()["auth_config"]["has_token"]
        async with async_session() as db:
            tool = await db.get(ToolDefinition, tool_id)
            assert tool.auth_config["token"].startswith("enc:v1:")
            assert decrypt_secret(tool.auth_config["token"]) == "offline-original-token"
        edited = await client.put(f"/api/v1/tools/{tool_id}", json={
            "description": "new label", "auth_config": created.json()["auth_config"],
        })
        assert edited.status_code == 200
        async with async_session() as db:
            assert decrypt_secret((await db.get(ToolDefinition, tool_id)).auth_config["token"]) == "offline-original-token"
        for secret in ("offline-rotated-token", ""):
            updated = await client.put(f"/api/v1/tools/{tool_id}", json={"auth_config": {"type": "bearer", "token": secret}})
            assert updated.status_code == 200
            assert updated.json()["auth_config"]["has_token"] == bool(secret)
            async with async_session() as db:
                assert decrypt_secret((await db.get(ToolDefinition, tool_id)).auth_config["token"]) == secret


async def test_legacy_credentials_hidden_and_migrated_idempotently():
    async with async_session() as db:
        tool = ToolDefinition(name="legacy-" + uuid.uuid4().hex, tenant_id="default",
                              auth_config={"type": "api_key", "token": "offline-legacy-token", "extra_secret": "never-visible"})
        db.add(tool)
        await db.commit()
        tool_id = tool.id
        await migrate_plaintext_tool_tokens(db)
        await db.refresh(tool)
        assert tool.auth_config["token"].startswith("enc:v1:")
        assert await migrate_plaintext_tool_tokens(db) == 0
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/api/v1/tools/", f"/api/v1/tools/{tool_id}"):
            response = await client.get(path)
            assert response.status_code == 200
            assert "offline-legacy-token" not in response.text
            assert "never-visible" not in response.text
            assert "enc:v1:" not in response.text


async def test_gateway_decrypts_only_for_authorized_http_request(monkeypatch):
    from server.api.tools import _store_auth_config
    seen = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, endpoint, *, json, headers):
            seen.append(headers["Authorization"])
            return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", endpoint))

    monkeypatch.setattr("server.engine.tool_gateway.httpx.AsyncClient", Client)
    tool = ToolDefinition(name="http-test", endpoint="https://example.invalid/query", method="POST", timeout_ms=1000,
                          auth_config=_store_auth_config({"type": "bearer", "token": "offline-token"}))
    assert await ToolGateway(None)._call_http_tool(tool, {}) == {"ok": True}
    assert seen == ["Bearer offline-token"]


@pytest.mark.parametrize("method", ["put", "delete"])
async def test_managed_query_cannot_be_repointed_or_deleted_through_generic_tools(method):
    async with async_session() as db:
        tool = ToolDefinition(name="managed-" + uuid.uuid4().hex, category="data_query", endpoint="source-id", tenant_id="default")
        db.add(tool)
        await db.commit()
        tool_id = tool.id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        kwargs = {"json": {"endpoint": "other-tenant-source"}} if method == "put" else {}
        result = await client.request(method, f"/api/v1/tools/{tool_id}", **kwargs)
        assert result.status_code == 409
        create = await client.post("/api/v1/tools/", json={"name": "spoof", "category": "data_query", "endpoint": "other-source"})
        assert create.status_code == 422
