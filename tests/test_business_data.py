"""Offline proofs for structured business data, tenant isolation and bounded SQL."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import date

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from server.api.business_data import router
from server.db import async_session
from server.engine import business_data
from server.engine.business_data import BusinessDataError, execute_data_query
from server.middleware.auth import get_current_user
from server.models.business_data import BusinessDataSource
from server.models.skill import Skill
from server.models.tool import ToolDefinition
from server.models.workflow import Workflow, WorkflowStep

FIELDS = [
    {"name": "order_id", "type": "string"},
    {"name": "amount", "type": "number"},
    {"name": "paid", "type": "boolean"},
    {"name": "due", "type": "date", "required": False},
]


@pytest_asyncio.fixture
async def client():
    user = {"id": uuid.uuid4().hex, "tenant_id": uuid.uuid4().hex, "role": "admin"}
    app = FastAPI()
    app.include_router(router, prefix="/data-sources")
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, user


async def create_source(client, **overrides):
    body = {"name": "Orders", "kind": "table", "fields": FIELDS,
            "filter_fields": ["order_id", "paid"], "required_filters": ["order_id"], "max_rows": 2}
    body.update(overrides)
    response = await client.post("/data-sources/", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def record(order_id="A-1", amount=12.5, paid=True, due="2026-09-22"):
    return {"order_id": order_id, "amount": amount, "paid": paid, "due": due}


async def test_table_tool_query_and_record_crud(client):
    ac, user = client
    source = await create_source(ac)
    async with async_session() as db:
        tool = await db.get(ToolDefinition, source["tool_id"])
        assert tool.category == "data_query" and tool.endpoint == source["id"]
        assert tool.max_retries == 0
        assert tool.input_schema["properties"]["filters"]["additionalProperties"] is False
        assert tool.input_schema["properties"]["filters"]["required"] == ["order_id"]
    created = await ac.post(f"/data-sources/{source['id']}/records", json={"values": record()})
    assert created.status_code == 201
    row_id = created.json()["id"]
    queried = await ac.post(f"/data-sources/{source['id']}/query", json={"filters": {"order_id": "A-1"}})
    assert queried.status_code == 200
    assert queried.json()["rows"] == [record()]
    assert queried.json()["row_count"] == 1
    updated = await ac.put(f"/data-sources/{source['id']}/records/{row_id}", json={"values": record(amount=20)})
    assert updated.status_code == 200
    assert updated.json()["values"]["amount"] == 20
    removed = await ac.delete(f"/data-sources/{source['id']}/records/{row_id}")
    assert removed.status_code == 204
    listed = await ac.get(f"/data-sources/{source['id']}/records")
    assert listed.json()["total"] == 0


async def test_query_is_typed_bounded_and_requires_configured_filters(client):
    ac, _ = client
    source = await create_source(ac)
    path = f"/data-sources/{source['id']}"
    await ac.post(path + "/records/import", json={"records": [record(amount=n) for n in (1, 2, 3)]})
    for body in ({"filters": {}}, {"filters": {"order_id": 123}},
                 {"filters": {"order_id": "A-1", "amount": 1}},
                 {"filters": {"order_id": "A-1"}, "limit": 3},
                 {"filters": {"order_id": "A-1", "paid": "true"}}):
        assert (await ac.post(path + "/query", json=body)).status_code == 422
    queried = await ac.post(path + "/query", json={"filters": {"order_id": "A-1"}})
    assert queried.json()["row_count"] == 2
    assert queried.json()["truncated"] is True
    missing = await ac.post(path + "/query", json={"filters": {"order_id": "absent"}})
    assert missing.json()["rows"] == [] and missing.json()["row_count"] == 0


async def test_import_validation_is_atomic_and_csv_converts_types(client):
    ac, _ = client
    source = await create_source(ac)
    path = f"/data-sources/{source['id']}/records"
    invalid = await ac.post(path + "/import", json={"records": [record(), record(paid="yes")]})
    assert invalid.status_code == 422
    assert (await ac.get(path)).json()["total"] == 0
    csv = b"order_id,amount,paid,due\nA-1,12.5,true,2026-09-22\nA-2,3,false,\n"
    imported = await ac.post(path + "/import-csv", files={"file": ("orders.csv", csv, "text/csv")})
    assert imported.status_code == 201
    assert imported.json()["imported"] == 2
    rows = sorted((await ac.get(path)).json()["items"], key=lambda row: row["values"]["order_id"])
    assert rows[0]["values"] == record()
    assert rows[1]["values"] == record("A-2", 3, False, None)
    invalid_csv = b"order_id,amount,paid,due\nA-3,9,true,\nA-4,9,not-boolean,\n"
    rejected = await ac.post(path + "/import-csv", files={"file": ("bad.csv", invalid_csv, "text/csv")})
    assert rejected.status_code == 422
    assert (await ac.get(path)).json()["total"] == 2


@pytest.mark.parametrize("operation", ["record", "json_import", "csv_import"])
async def test_schema_change_waits_for_record_mutation_commit(client, monkeypatch, operation):
    """An empty-table schema change cannot pass a not-yet-committed import."""
    from server.api import business_data as api

    ac, _ = client
    source = await create_source(ac)
    path = f"/data-sources/{source['id']}"
    committing, release_commit = asyncio.Event(), asyncio.Event()
    original_table, original_commit = api._table, AsyncSession.commit

    async def mark_record_session(db, source_id, tenant_id):
        result = await original_table(db, source_id, tenant_id)
        db.info["pause_record_commit"] = True
        return result

    async def delayed_commit(db):
        if db.info.pop("pause_record_commit", False):
            committing.set()
            await release_commit.wait()
        await original_commit(db)

    monkeypatch.setattr(api, "_table", mark_record_session)
    monkeypatch.setattr(AsyncSession, "commit", delayed_commit)
    if operation == "record":
        request = ac.post(path + "/records", json={"values": record()})
    elif operation == "json_import":
        request = ac.post(path + "/records/import", json={"records": [record()]})
    else:
        request = ac.post(path + "/records/import-csv", files={
            "file": ("orders.csv", b"order_id,amount,paid,due\nA-1,12.5,true,2026-09-22\n", "text/csv"),
        })
    insertion = asyncio.create_task(request)
    schema_update = None
    try:
        await asyncio.wait_for(committing.wait(), timeout=5)
        schema_update = asyncio.create_task(ac.put(path, json={
            "fields": [{"name": "replacement", "type": "integer"}],
            "filter_fields": [], "required_filters": [],
        }))
        # It must wait through the actual commit, not only validation/flush.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(schema_update), timeout=0.1)
        release_commit.set()
        inserted, updated = await asyncio.wait_for(asyncio.gather(insertion, schema_update), timeout=5)
        assert inserted.status_code == 201
        assert updated.status_code == 409
        saved = (await ac.get(path)).json()
        assert saved["fields"] == source["fields"]
        queried = await ac.post(path + "/query", json={"filters": {"order_id": "A-1"}})
        assert queried.json()["rows"] == [record()]
    finally:
        release_commit.set()
        await asyncio.gather(insertion, *([schema_update] if schema_update else []), return_exceptions=True)


async def test_cross_tenant_source_and_records_are_hidden(client):
    ac, user = client
    source = await create_source(ac)
    row = (await ac.post(f"/data-sources/{source['id']}/records", json={"values": record()})).json()
    user["tenant_id"] = uuid.uuid4().hex
    assert (await ac.get("/data-sources/")).json() == []
    for method, path, body in [
        ("GET", f"/{source['id']}", None),
        ("GET", f"/{source['id']}/records", None),
        ("POST", f"/{source['id']}/query", {"filters": {"order_id": "A-1"}}),
        ("DELETE", f"/{source['id']}/records/{row['id']}", None),
        ("DELETE", f"/{source['id']}", None),
    ]:
        assert (await ac.request(method, "/data-sources" + path, json=body)).status_code == 404
    async with async_session() as db:
        with pytest.raises(BusinessDataError):
            await execute_data_query(db, source["id"], user["tenant_id"], {"filters": {"order_id": "A-1"}})


async def test_source_delete_rejects_skill_references_then_removes_tool(client):
    ac, user = client
    source = await create_source(ac)
    async with async_session() as db:
        skill = Skill(name="Query orders", skill_type="tool_call", tenant_id=user["tenant_id"],
                      execution_config={"tool_ids": [source["tool_id"]]})
        db.add(skill)
        await db.commit()
        skill_id = skill.id
    assert (await ac.delete(f"/data-sources/{source['id']}")).status_code == 409
    async with async_session() as db:
        await db.delete(await db.get(Skill, skill_id))
        await db.commit()
    assert (await ac.delete(f"/data-sources/{source['id']}")).status_code == 204
    async with async_session() as db:
        assert await db.get(ToolDefinition, source["tool_id"]) is None


async def test_postgres_credentials_are_admin_only_encrypted_and_write_only(client):
    ac, user = client
    dsn = "postgresql://tester:offline-password@localhost/orders"
    user["role"] = "editor"
    denied = await ac.post("/data-sources/", json={"name": "Orders", "kind": "postgres", "fields": FIELDS,
                                                "postgres_dsn": dsn, "postgres_table": "orders"})
    assert denied.status_code == 403
    user["role"] = "admin"
    source = await create_source(ac, kind="postgres", postgres_dsn=dsn, postgres_table="orders")
    for result in (source, (await ac.get("/data-sources/")).json(),
                   (await ac.get(f"/data-sources/{source['id']}")).json()):
        assert "offline-password" not in str(result)
        assert "postgres_dsn" not in str(result)
    assert source["has_credentials"] is True
    async with async_session() as db:
        stored = await db.get(BusinessDataSource, source["id"])
        assert stored.postgres_dsn_encrypted.startswith("enc:v1:")
        assert dsn not in stored.postgres_dsn_encrypted
    user["role"] = "editor"
    assert (await ac.put(f"/data-sources/{source['id']}", json={"postgres_table": "other"})).status_code == 403


@pytest.fixture
def postgres(monkeypatch):
    class Connection:
        def __init__(self):
            self.calls = []
            self.closed = False
            self.rows = [record()]

        @asynccontextmanager
        async def transaction(self, **kwargs):
            self.transaction_options = kwargs
            yield

        async def execute(self, sql):
            self.calls.append((sql, ()))

        async def fetch(self, sql, *args):
            self.calls.append((sql, args))
            return self.rows

        async def close(self, **kwargs):
            self.closed = True

        def terminate(self):
            self.closed = True

    connection = Connection()

    async def connect(*args, **kwargs):
        connection.connect_options = kwargs
        return connection

    monkeypatch.setattr(business_data.asyncpg, "connect", connect)
    return connection


async def test_postgres_queries_only_bound_filters_and_server_owned_tenant(client, postgres):
    ac, user = client
    source = await create_source(ac, kind="postgres", postgres_dsn="postgresql://localhost/orders",
                                 postgres_schema="business", postgres_table="orders", tenant_column="tenant_id")
    injected = "x' OR 1=1 --"
    result = await ac.post(f"/data-sources/{source['id']}/query", json={"filters": {"order_id": injected}})
    assert result.status_code == 200
    sql, parameters = postgres.calls[-1]
    assert 'FROM "business"."orders"' in sql
    assert '"tenant_id" = $' in sql
    assert injected not in sql and injected in parameters
    assert user["tenant_id"] in parameters
    assert parameters[-1] == 3
    assert postgres.transaction_options == {"readonly": True}
    assert "statement_timeout" in postgres.calls[0][0]
    assert postgres.connect_options["timeout"] <= 5
    assert postgres.closed
    assert (await ac.post(f"/data-sources/{source['id']}/query", json={
        "filters": {"order_id": "A-1", "tenant_id": "someone-else"},
    })).status_code == 422


async def test_postgres_failure_hides_connection_secrets_and_closes(client, postgres, monkeypatch):
    ac, _ = client
    source = await create_source(ac, kind="postgres", postgres_dsn="postgresql://u:secret@localhost/db", postgres_table="orders")

    async def fail(*args):
        raise RuntimeError("postgresql://u:secret@localhost/db")

    monkeypatch.setattr(postgres, "fetch", fail)
    response = await ac.post(f"/data-sources/{source['id']}/query", json={"filters": {"order_id": "A-1"}})
    assert response.status_code == 502
    assert "secret" not in response.text and "postgresql://" not in response.text
    assert postgres.closed


async def test_field_changes_cannot_invalidate_saved_records(client):
    ac, _ = client
    source = await create_source(ac)
    await ac.post(f"/data-sources/{source['id']}/records", json={"values": record()})
    changed = [dict(field) for field in FIELDS]
    changed[0]["type"] = "integer"
    rejected = await ac.put(f"/data-sources/{source['id']}", json={"fields": changed})
    assert rejected.status_code == 409
    unchanged = await ac.get(f"/data-sources/{source['id']}")
    assert unchanged.json()["fields"][0]["type"] == "string"


async def test_scope_role_and_generated_tool_updates(client):
    ac, user = client
    user["role"] = "editor"
    source = await create_source(ac)
    changed = await ac.put(f"/data-sources/{source['id']}", json={"name": "Renamed", "max_rows": 5, "enabled": False})
    assert changed.status_code == 200
    async with async_session() as db:
        tool = await db.get(ToolDefinition, source["tool_id"])
        assert tool.name == changed.json()["tool_name"]
        assert tool.input_schema["properties"]["limit"]["maximum"] == 5
        assert tool.enabled is False
        with pytest.raises(BusinessDataError):
            await execute_data_query(db, source["id"], user["tenant_id"], {"filters": {"order_id": "A-1"}})
    user["role"] = "viewer"
    assert (await ac.get(f"/data-sources/{source['id']}")).status_code == 200
    assert (await ac.post(f"/data-sources/{source['id']}/records", json={"values": record()})).status_code == 403
    user["role"] = "admin"
    user["api_key_scopes"] = ["invoke"]
    assert (await ac.get("/data-sources/")).status_code == 403


async def test_workflow_reference_blocks_source_deletion(client):
    ac, user = client
    source = await create_source(ac)
    async with async_session() as db:
        workflow = Workflow(name="Orders workflow", tenant_id=user["tenant_id"])
        db.add(workflow)
        await db.flush()
        db.add(WorkflowStep(workflow_id=workflow.id, name="Query", order=0, step_type="tool_call", tool_id=source["tool_id"]))
        await db.commit()
    assert (await ac.delete(f"/data-sources/{source['id']}")).status_code == 409


async def test_integer_boolean_date_filters_are_strict(client, postgres):
    ac, _ = client
    fields = [{"name": "quantity", "type": "integer"}, {"name": "due", "type": "date"}]
    source = await create_source(ac, kind="postgres", fields=fields,
                                 filter_fields=["quantity", "due"], required_filters=["quantity"],
                                 postgres_dsn="postgresql://localhost/orders", postgres_table="orders")
    path = f"/data-sources/{source['id']}/query"
    for quantity in (True, 1.5, "1"):
        assert (await ac.post(path, json={"filters": {"quantity": quantity}})).status_code == 422
    assert (await ac.post(path, json={"filters": {"quantity": 1, "due": "2026-02-30"}})).status_code == 422
    assert not postgres.calls
    postgres.rows = [{"quantity": 1, "due": date(2026, 9, 22)}]
    result = await ac.post(path, json={"filters": {"quantity": 1, "due": "2026-09-22"}})
    assert result.status_code == 200
    assert date(2026, 9, 22) in postgres.calls[-1][1]
    assert result.json()["rows"] == [{"quantity": 1, "due": "2026-09-22"}]


async def test_csv_size_rows_and_headers_are_bounded(client):
    ac, _ = client
    source = await create_source(ac)
    path = f"/data-sources/{source['id']}/records"
    payloads = [
        (b"x" * (business_data.MAX_IMPORT_BYTES + 1), 413),
        (b"order_id,amount,paid,due\n" + b"A,1,true,\n" * 1001, 422),
        (b"order_id,order_id\nA,A\n", 422),
        (b"order_id,amount,paid,extra\nA,1,true,x\n", 422),
    ]
    for content, status in payloads:
        result = await ac.post(path + "/import-csv", files={"file": ("rows.csv", content, "text/csv")})
        assert result.status_code == status
    assert (await ac.get(path)).json()["total"] == 0


@pytest.mark.parametrize("invalid", [
    {"postgres_table": 'orders"; DROP TABLE users; --'},
    {"fields": [{"name": 'id"', "type": "string"}]},
    {"tenant_column": "tenant_id", "filter_fields": ["tenant_id"], "fields": [{"name": "tenant_id", "type": "string"}]},
])
async def test_sql_configuration_rejects_identifiers_and_spoofable_tenant_filters(client, invalid):
    ac, _ = client
    body = {"name": "Orders", "kind": "postgres", "fields": FIELDS,
            "postgres_dsn": "postgresql://u:do-not-return-this@localhost/db", "postgres_table": "orders"}
    body.update(invalid)
    result = await ac.post("/data-sources/", json=body)
    assert result.status_code == 422
    assert "do-not-return-this" not in result.text


@pytest.mark.parametrize("state", ["fresh", "versioned", "unversioned"])
async def test_business_data_migrations_preserve_existing_data(tmp_path, monkeypatch, state):
    import asyncio
    from alembic import command
    from server import db_migrate
    from server.config import settings

    url = f"sqlite+aiosqlite:///{(tmp_path / 'migration.db').as_posix()}"
    engine = create_async_engine(url)
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(db_migrate, "engine", engine)
    try:
        if state != "fresh":
            await asyncio.to_thread(command.upgrade, db_migrate._alembic_config(), "0001")
            async with engine.begin() as connection:
                await connection.execute(text("INSERT INTO users (id,username,password_hash,role,tenant_id,display_name,enabled,created_at,updated_at) VALUES ('existing','existing','unused','admin','existing','Existing',true,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                if state == "unversioned":
                    await connection.execute(text("DROP TABLE alembic_version"))
        await db_migrate.ensure_schema()
        await db_migrate.ensure_schema()
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
            assert {"business_data_sources", "business_records"}.issubset(tables)
            assert (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar() == "0002"
            if state != "fresh":
                assert (await connection.execute(text("SELECT username FROM users WHERE id='existing'"))).scalar() == "existing"
    finally:
        await engine.dispose()
