"""Public regressions for credential escalation and management authorization."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.config import settings
from server.db import Base, async_session, get_db
from server.main import app
from server.middleware.auth import create_jwt_token, hash_api_key
from server.models.user import APIKey, User

PREFIX = settings.api_prefix
_JWT = object()


@pytest_asyncio.fixture
async def authenticated_client(monkeypatch):
    monkeypatch.setattr(settings, "disable_auth", False)

    async def headers(role="admin", scopes=_JWT):
        identifier = uuid.uuid4().hex
        async with async_session() as db:
            user = User(
                id=identifier,
                username=f"auth-test-{identifier}",
                password_hash="unused-test-hash",
                role=role,
                tenant_id=identifier,
            )
            db.add(user)
            if scopes is _JWT:
                credentials = {"Authorization": f"Bearer {create_jwt_token(identifier, identifier, role)}"}
            else:
                raw_key = f"offline-test-{identifier}"
                db.add(APIKey(user_id=identifier, tenant_id=identifier,
                              key_hash=hash_api_key(raw_key), scopes=scopes))
                credentials = {"X-API-Key": raw_key}
            await db.commit()
        return credentials

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, headers


@pytest.mark.parametrize("scopes", [["invoke"], ["manage"], [], None])
@pytest.mark.parametrize("requested_scopes", [None, [], ["invoke"], ["manage"]])
async def test_api_keys_cannot_issue_another_key(authenticated_client, scopes, requested_scopes):
    client, headers = authenticated_client
    response = await client.post(
        f"{PREFIX}/auth/api-keys", headers=await headers(scopes=scopes),
        json={"name": "must-not-be-issued", "scopes": requested_scopes},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["register", "register-admin"])
@pytest.mark.parametrize("scopes", [["invoke"], ["manage"], None])
async def test_api_keys_cannot_create_login_credentials(authenticated_client, path, scopes):
    client, headers = authenticated_client
    response = await client.post(
        f"{PREFIX}/auth/{path}", headers=await headers(scopes=scopes),
        json={"username": f"rejected-{uuid.uuid4().hex}", "password": "offline-test-password"},
    )
    assert response.status_code == 403


async def test_invoke_key_cannot_list_or_revoke_owner_keys(authenticated_client):
    client, headers = authenticated_client
    credentials = await headers(scopes=["invoke"])
    listed = await client.get(f"{PREFIX}/auth/api-keys", headers=credentials)
    revoked = await client.delete(f"{PREFIX}/auth/api-keys/any-key", headers=credentials)
    assert listed.status_code == revoked.status_code == 403


@pytest.mark.parametrize("role", ["viewer", "editor", "admin"])
async def test_jwt_can_manage_own_keys(authenticated_client, role):
    client, headers = authenticated_client
    credentials = await headers(role=role)
    created = await client.post(
        f"{PREFIX}/auth/api-keys", headers=credentials,
        json={"name": "integration", "scopes": ["invoke"]},
    )
    assert created.status_code == 201
    body = created.json()
    listed = await client.get(f"{PREFIX}/auth/api-keys", headers=credentials)
    assert listed.status_code == 200
    assert body["id"] in {item["id"] for item in listed.json()}
    assert all("key" not in item for item in listed.json())
    revoked = await client.delete(f"{PREFIX}/auth/api-keys/{body['id']}", headers=credentials)
    assert revoked.status_code == 204
    invalid = await client.get(f"{PREFIX}/auth/me", headers={"X-API-Key": body["key"]})
    assert invalid.status_code == 401


@pytest.mark.parametrize("path", ["register", "register-admin"])
@pytest.mark.parametrize("role,expected", [("admin", 201), ("editor", 403), ("viewer", 403)])
async def test_login_user_creation_requires_admin(authenticated_client, path, role, expected):
    client, headers = authenticated_client
    response = await client.post(
        f"{PREFIX}/auth/{path}", headers=await headers(role=role),
        json={"username": f"created-{uuid.uuid4().hex}", "password": "offline-test-password"},
    )
    assert response.status_code == expected


async def test_first_admin_bootstrap_still_works(authenticated_client):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def isolated_db():
        async with session_maker() as db:
            yield db
            await db.commit()

    app.dependency_overrides[get_db] = isolated_db
    try:
        client, _ = authenticated_client
        created = await client.post(
            f"{PREFIX}/auth/register",
            json={"username": "bootstrap-admin", "password": "offline-test-password"},
        )
        assert created.status_code == 201
        assert created.json()["role"] == "admin"
        rejected = await client.post(
            f"{PREFIX}/auth/register",
            json={"username": "second-anonymous", "password": "offline-test-password"},
        )
        assert rejected.status_code == 403
    finally:
        del app.dependency_overrides[get_db]
        await engine.dispose()


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/tools/", {"name": "forbidden-tool"}),
    ("PUT", "/tools/missing", {"name": "forbidden-update"}),
    ("DELETE", "/tools/missing", None),
    ("POST", "/workflows/", {"name": "forbidden-workflow"}),
])
@pytest.mark.parametrize("scopes", [_JWT, ["manage"], None])
async def test_viewers_cannot_mutate_management_resources(authenticated_client, method, path, body, scopes):
    client, headers = authenticated_client
    response = await client.request(
        method, f"{PREFIX}{path}", headers=await headers(role="viewer", scopes=scopes), json=body,
    )
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["editor", "admin"])
@pytest.mark.parametrize("scopes", [_JWT, ["manage"], None])
async def test_editors_and_admins_can_manage_tools(authenticated_client, role, scopes):
    client, headers = authenticated_client
    credentials = await headers(role=role, scopes=scopes)
    created = await client.post(
        f"{PREFIX}/tools/", headers=credentials, json={"name": f"tool-{uuid.uuid4().hex}"},
    )
    assert created.status_code == 201
    tool_id = created.json()["id"]
    updated = await client.put(
        f"{PREFIX}/tools/{tool_id}", headers=credentials, json={"description": "updated"},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "updated"
    deleted = await client.delete(f"{PREFIX}/tools/{tool_id}", headers=credentials)
    assert deleted.status_code == 204


async def test_viewer_can_read_management_and_invoke(authenticated_client, monkeypatch):
    from server.engine.agent_runtime import AgentRuntime
    from server.schemas.invoke import InvokeResponse

    async def invoke(self, request):
        return InvokeResponse(session_id="test", trace_id="test", short_answer="ok")

    monkeypatch.setattr(AgentRuntime, "invoke", invoke)
    client, headers = authenticated_client
    credentials = await headers(role="viewer")
    listed = await client.get(f"{PREFIX}/tools/", headers=credentials)
    invoked = await client.post(
        f"{PREFIX}/invoke", headers=credentials, json={"agent_id": "test", "message": "hello"},
    )
    assert listed.status_code == invoked.status_code == 200
    assert invoked.json()["short_answer"] == "ok"


@pytest.fixture
def stub_global_mutations(monkeypatch):
    from server.api import asr, performance, vector_admin
    from server.engine.asr import ASRConfig

    calls = []
    monkeypatch.setattr(asr, "save_asr_config_update", lambda update: calls.append("asr") or ASRConfig(provider="disabled"))
    monkeypatch.setattr(asr, "_config_response", lambda config: {
        "enabled": False, "provider": "disabled", "base_url": "", "model": "", "max_file_mb": 10,
        "timeout": 60, "funasr_path": "/transcribe", "config_path": "unused",
    })
    monkeypatch.setattr(performance, "runtime_config", SimpleNamespace(
        update=lambda config: calls.append("performance"), set=lambda key, value: None, all=lambda: {},
    ))

    async def rebuild():
        calls.append("vector")
        return {"status": "ok"}

    monkeypatch.setattr(vector_admin, "rebuild_vector_index", rebuild)
    monkeypatch.setattr(vector_admin.EmbeddingModel, "_instance", object())
    return calls


@pytest.mark.parametrize("method,path,body", [
    ("PUT", "/asr/config", {"provider": "disabled"}),
    ("POST", "/performance/update-config", {"config": {"retrieval_top_k": 1}}),
    ("POST", "/performance/presets/apply", {"preset": "fast"}),
    ("POST", "/vector-admin/rebuild", None),
    ("POST", "/vector-admin/warmup", None),
])
@pytest.mark.parametrize("role,scopes,expected", [
    ("admin", ["invoke"], 403),
    ("viewer", _JWT, 403),
    ("editor", _JWT, 403),
    ("admin", _JWT, 200),
    ("admin", ["manage"], 200),
])
async def test_global_mutations_require_admin_and_manage_scope(
    authenticated_client, stub_global_mutations, method, path, body, role, scopes, expected,
):
    client, headers = authenticated_client
    response = await client.request(
        method, f"{PREFIX}{path}", headers=await headers(role=role, scopes=scopes), json=body,
    )
    assert response.status_code == expected
    if expected == 403:
        assert not stub_global_mutations


@pytest.mark.parametrize("method,path", [
    ("GET", "/backups/"), ("POST", "/backups/"),
    ("GET", "/backups/test.zip/download"), ("DELETE", "/backups/test.zip"),
])
async def test_invoke_key_cannot_access_backups(authenticated_client, monkeypatch, method, path):
    from server.api import backup

    def unexpected_access(*args):
        pytest.fail("Backup storage must not be accessed with an invoke key")

    monkeypatch.setattr(backup, "list_backups", unexpected_access)
    monkeypatch.setattr(backup, "create_backup", unexpected_access)
    monkeypatch.setattr(backup, "resolve_backup_path", unexpected_access)
    client, headers = authenticated_client
    response = await client.request(method, f"{PREFIX}{path}", headers=await headers(scopes=["invoke"]))
    assert response.status_code == 403


@pytest.mark.parametrize("scopes", [_JWT, ["manage"]])
async def test_admin_can_read_backups(authenticated_client, monkeypatch, scopes):
    from server.api import backup

    monkeypatch.setattr(backup, "list_backups", lambda: [])
    client, headers = authenticated_client
    response = await client.get(f"{PREFIX}/backups/", headers=await headers(scopes=scopes))
    assert response.status_code == 200


@pytest.fixture
def stub_asr(monkeypatch):
    from server.api import asr
    from server.engine.asr import ASRConfig

    calls = []

    class OfflineASR:
        config = ASRConfig(provider="openai_compatible", max_file_mb=1)

        async def transcribe(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text="hello", provider="openai_compatible", model="test", language=None,
                                   duration_seconds=None, raw={})

    monkeypatch.setattr(asr, "ASRService", OfflineASR)
    return calls


async def test_asr_transcription_requires_invoke_scope(authenticated_client, stub_asr):
    client, headers = authenticated_client
    denied = await client.post(
        f"{PREFIX}/asr/transcribe", headers=await headers(scopes=["manage"]),
        files={"file": ("test.wav", b"audio", "audio/wav")},
    )
    assert denied.status_code == 403
    assert not stub_asr
    allowed = await client.post(
        f"{PREFIX}/asr/transcribe", headers=await headers(role="viewer", scopes=["invoke"]),
        files={"file": ("test.wav", b"audio", "audio/wav")},
    )
    assert allowed.status_code == 200
    assert len(stub_asr) == 1


async def test_asr_transcription_is_rate_limited(authenticated_client, stub_asr, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    client, headers = authenticated_client
    credentials = await headers(scopes=["invoke"])
    for expected in (200, 429):
        response = await client.post(
            f"{PREFIX}/asr/transcribe", headers=credentials,
            files={"file": ("test.wav", b"audio", "audio/wav")},
        )
        assert response.status_code == expected
    assert len(stub_asr) == 1


async def test_asr_oversized_audio_never_reaches_provider(authenticated_client, stub_asr):
    client, headers = authenticated_client
    response = await client.post(
        f"{PREFIX}/asr/transcribe", headers=await headers(scopes=["invoke"]),
        files={"file": ("large.wav", b"a" * (1024 * 1024 + 1), "audio/wav")},
    )
    assert response.status_code == 413
    assert not stub_asr


async def test_asr_read_is_bounded_by_provider_limit(stub_asr):
    from server.api.asr import transcribe_audio

    class OversizedUpload:
        filename = "large.wav"

        async def read(self, size):
            assert size == 1024 * 1024 + 1
            return b"a" * size

    with pytest.raises(HTTPException) as raised:
        await transcribe_audio(file=OversizedUpload(), language=None, prompt=None)
    assert raised.value.status_code == 413
    assert not stub_asr
