"""Password work and callback connections enforce their actual runtime boundaries."""
from __future__ import annotations

import socket
import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.engine import event_dispatcher as events
from server.middleware import auth


async def test_login_attempts_have_independent_ip_budget(monkeypatch):
    monkeypatch.setattr(auth.time, "monotonic", lambda: 1000)
    with auth._rate_limit_lock:
        auth._rate_limit_windows.clear()
    request = Request({"type": "http", "client": ("192.0.2.123", 1234), "headers": []})
    for _ in range(10):
        await auth.enforce_login_rate_limit(request)
    with pytest.raises(HTTPException) as error:
        await auth.enforce_login_rate_limit(request)
    assert error.value.status_code == 429
    assert int(error.value.headers["Retry-After"]) > 0
    second = Request({"type": "http", "client": ("192.0.2.124", 1234), "headers": []})
    await auth.enforce_login_rate_limit(second)
    with auth._rate_limit_lock:
        auth._rate_limit_windows.clear()


async def test_password_verification_runs_outside_event_loop(monkeypatch):
    import importlib
    api = importlib.import_module("server.api.auth")
    from server.schemas.auth import LoginRequest
    loop_thread = threading.get_ident()
    seen = []

    def verify(password, stored):
        seen.append(threading.get_ident())
        return False

    async def execute(query):
        return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(password_hash="test-hash"))

    monkeypatch.setattr(api, "verify_password", verify)
    with pytest.raises(HTTPException) as error:
        await api.login(LoginRequest(username="synthetic-user", password="incorrect"), SimpleNamespace(execute=execute))
    assert error.value.status_code == 401
    assert seen and seen[0] != loop_thread


async def test_concurrent_first_registration_creates_exactly_one_admin(tmp_path, monkeypatch):
    import importlib
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from server.db import Base, get_db
    from server.main import app
    from server.models.user import User
    api = importlib.import_module("server.api.auth")
    monkeypatch.setattr(api.settings, "disable_auth", False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'bootstrap.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db_override():
        async with sessions() as db:
            yield db

    loop = asyncio.get_running_loop()
    started, release = asyncio.Event(), threading.Event()

    def hash_password(password):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(2)
        return "synthetic-hash"

    monkeypatch.setattr(api, "hash_password", hash_password)
    app.dependency_overrides[get_db] = db_override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/api/v1/auth/register", json={"username": "first-admin", "password": "offline-password"}))
            await asyncio.wait_for(started.wait(), timeout=2)
            second = asyncio.create_task(client.post("/api/v1/auth/register", json={"username": "second-admin", "password": "offline-password"}))
            await asyncio.sleep(0.02)
            release.set()
            responses = await asyncio.gather(first, second)
            assert [r.status_code for r in responses] == [201, 403]
        async with sessions() as db:
            users = (await db.scalars(select(User))).all()
            assert len(users) == 1 and users[0].role == "admin"
    finally:
        release.set()
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


def _dns(address):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]


async def test_callback_pins_checked_ip_and_preserves_host_and_tls_name(monkeypatch):
    monkeypatch.setattr(events, "_allow_internal_webhooks", lambda: False)
    lookups, requests, options = [], [], []

    def resolve(host, port):
        lookups.append(host)
        return _dns("93.184.216.34" if len(lookups) == 1 else "127.0.0.1")

    def handle(request):
        requests.append(request)
        return httpx.Response(200)

    original = httpx.AsyncClient

    def client(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(events.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(events.httpx, "AsyncClient", client)
    await events._deliver_one(SimpleNamespace(id="test", url="https://hooks.example:8443/events", secret="offline-secret"), "done", b"{}")
    assert lookups == ["hooks.example"]
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["Host"] == "hooks.example:8443"
    assert requests[0].extensions["sni_hostname"] == "hooks.example"
    assert options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False


async def test_callback_rechecks_dns_before_retry(monkeypatch):
    monkeypatch.setattr(events, "_allow_internal_webhooks", lambda: False)
    monkeypatch.setattr(events, "RETRY_BACKOFF_S", 0)
    lookups, requests = [], []

    def resolve(host, port):
        lookups.append(host)
        return _dns("93.184.216.34" if len(lookups) == 1 else "169.254.169.254")

    def handle(request):
        requests.append(request)
        return httpx.Response(503)

    original = httpx.AsyncClient
    monkeypatch.setattr(events.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(events.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    await events._deliver_one(SimpleNamespace(id="test", url="https://hooks.example/events", secret="offline-secret"), "done", b"{}")
    assert len(lookups) == 2
    assert len(requests) == 1


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "100.64.0.1", "224.0.0.1"])
def test_callback_rejects_nonpublic_destination(monkeypatch, address):
    monkeypatch.setattr(events, "_allow_internal_webhooks", lambda: False)
    monkeypatch.setattr(events.socket, "getaddrinfo", lambda *args: _dns(address))
    with pytest.raises(events.WebhookTargetBlockedError):
        events.check_webhook_url("https://hooks.example/events")
