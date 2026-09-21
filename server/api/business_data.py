"""Manage tenant-owned business records and administrator-configured PostgreSQL sources."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.routing import APIRoute
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.db import get_db
from server.engine.business_data import (
    MAX_IMPORT_BYTES, BusinessDataError, execute_data_query, parse_csv, tool_input_schema,
    validate_configuration, validate_dsn, validate_import, validate_record,
)
from server.engine.request_guard import SessionWaitTimeout, session_lock
from server.engine.secrets_store import encrypt_secret
from server.middleware.auth import get_current_user, get_tenant_id, require_scope
from server.models.agent import Agent
from server.models.agent_skill import AgentSkill
from server.models.business_data import BusinessDataSource, BusinessRecord
from server.models.skill import Skill
from server.models.tool import ToolDefinition
from server.models.workflow import Workflow, WorkflowStep
from server.schemas.business_data import (
    DataQuery, DataSourceCreate, DataSourceOut, DataSourceUpdate,
    RecordImport, RecordOut, RecordWrite,
)


class BusinessDataRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await handler(request)
            except BusinessDataError as exc:
                raise HTTPException(exc.status_code, str(exc)) from None

        return handle


router = APIRouter(route_class=BusinessDataRoute, dependencies=[Depends(require_scope("manage"))])


async def _source_write(
    source_id: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
) -> AsyncIterator[AsyncSession]:
    """Keep schema and record mutations consistent within the single process."""
    try:
        async with session_lock(
            f"data-source:{user['tenant_id']}:{source_id}",
            timeout_seconds=settings.session_wait_timeout_seconds,
        ):
            try:
                yield db
                # get_db commits after dependency cleanup; commit here so the
                # next source writer cannot read before this change is durable.
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    except SessionWaitTimeout as exc:
        raise HTTPException(
            429, "This data source is being changed. Retry shortly.",
            headers={"Retry-After": "1"},
        ) from exc


def _admin_for_postgres(kind: str, user: dict) -> None:
    if kind == "postgres" and user.get("role") != "admin":
        raise HTTPException(403, "PostgreSQL source configuration requires admin role")


def _tool_name(source: BusinessDataSource) -> str:
    return f"{source.name[:80]} [data:{source.id.replace('-', '')}]"


def _out(source: BusinessDataSource) -> dict:
    fields = ("id", "tool_id", "tenant_id", "name", "description", "kind", "fields", "filter_fields",
              "required_filters", "max_rows", "postgres_schema", "postgres_table", "tenant_column",
              "enabled", "created_at", "updated_at")
    return {**{name: getattr(source, name) for name in fields},
            "tool_name": _tool_name(source), "has_credentials": bool(source.postgres_dsn_encrypted)}


async def _source(db: AsyncSession, source_id: str, tenant_id: str) -> BusinessDataSource:
    source = await db.scalar(select(BusinessDataSource).where(
        BusinessDataSource.id == source_id, BusinessDataSource.tenant_id == tenant_id,
    ))
    if source is None:
        raise HTTPException(404, "Data source not found")
    return source


async def _table(db: AsyncSession, source_id: str, tenant_id: str) -> BusinessDataSource:
    source = await _source(db, source_id, tenant_id)
    if source.kind != "table":
        raise HTTPException(400, "PostgreSQL sources are read-only; edit records in the business system")
    return source


def _configure_tool(tool: ToolDefinition, source: BusinessDataSource) -> None:
    tool.name = _tool_name(source)
    tool.description = f"Query business data from {source.name}. {source.description}".strip()
    tool.category = "data_query"
    tool.endpoint = source.id
    tool.input_schema = tool_input_schema(source)
    tool.max_retries = 0
    tool.enabled = source.enabled


@router.get("/", response_model=list[DataSourceOut])
async def list_sources(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.scalars(select(BusinessDataSource).where(
        BusinessDataSource.tenant_id == tenant_id,
    ).order_by(BusinessDataSource.created_at, BusinessDataSource.id))
    return [_out(source) for source in result.all()]


@router.post("/", response_model=DataSourceOut, status_code=201)
async def create_source(
    body: DataSourceCreate, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    _admin_for_postgres(body.kind, user)
    data = body.model_dump(exclude={"postgres_dsn"})
    dsn = body.postgres_dsn.get_secret_value() if body.postgres_dsn else ""
    validate_configuration(data, has_credentials=bool(dsn))
    if dsn:
        validate_dsn(dsn)
    if body.kind == "table":
        data["postgres_schema"] = None
    source = BusinessDataSource(
        id=str(uuid.uuid4()), tool_id=str(uuid.uuid4()), tenant_id=user["tenant_id"],
        postgres_dsn_encrypted=encrypt_secret(dsn) if dsn else None, **data,
    )
    tool = ToolDefinition(id=source.tool_id, tenant_id=user["tenant_id"])
    _configure_tool(tool, source)
    db.add(tool)
    await db.flush()
    db.add(source)
    await db.flush()
    await db.refresh(source)
    return _out(source)


@router.get("/{source_id}", response_model=DataSourceOut)
async def get_source(source_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return _out(await _source(db, source_id, tenant_id))


@router.put("/{source_id}", response_model=DataSourceOut)
async def update_source(
    source_id: str, body: DataSourceUpdate, user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_source_write),
):
    source = await _source(db, source_id, user["tenant_id"])
    _admin_for_postgres(source.kind, user)
    changes = body.model_dump(exclude_unset=True, exclude={"postgres_dsn"})
    if any(value is None and key != "tenant_column" for key, value in changes.items()):
        raise BusinessDataError("Configuration fields cannot be null except tenant_column")
    data = {**_out(source), **changes}
    dsn = body.postgres_dsn.get_secret_value() if body.postgres_dsn else None
    validate_configuration(data, has_credentials=bool(dsn or source.postgres_dsn_encrypted))
    if dsn is not None:
        validate_dsn(dsn)
    if source.kind == "table" and changes.get("fields", source.fields) != source.fields:
        count = await db.scalar(select(func.count()).select_from(BusinessRecord).where(
            BusinessRecord.source_id == source.id, BusinessRecord.tenant_id == user["tenant_id"],
        ))
        if count:
            raise HTTPException(409, "Remove existing records before changing their field schema")
    for key, value in changes.items():
        setattr(source, key, value)
    if dsn is not None:
        source.postgres_dsn_encrypted = encrypt_secret(dsn)
    tool = await db.scalar(select(ToolDefinition).where(
        ToolDefinition.id == source.tool_id, ToolDefinition.tenant_id == user["tenant_id"],
    ))
    if tool is None:
        raise HTTPException(409, "Generated data query tool is missing")
    _configure_tool(tool, source)
    await db.flush()
    await db.refresh(source)
    return _out(source)


async def _has_tool_references(db: AsyncSession, tenant_id: str, tool_id: str) -> bool:
    configs = await db.scalars(select(Skill.execution_config).where(Skill.tenant_id == tenant_id))
    if any(tool_id in (config or {}).get("tool_ids", []) for config in configs):
        return True
    overrides = await db.scalars(select(AgentSkill.config_override).join(
        Agent, Agent.id == AgentSkill.agent_id,
    ).where(Agent.tenant_id == tenant_id))
    if any(tool_id in (config or {}).get("tool_ids", []) for config in overrides):
        return True
    return await db.scalar(select(WorkflowStep.id).join(Workflow).where(
        Workflow.tenant_id == tenant_id, WorkflowStep.tool_id == tool_id,
    ).limit(1)) is not None


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(_source_write),
):
    source = await _source(db, source_id, user["tenant_id"])
    _admin_for_postgres(source.kind, user)
    if await _has_tool_references(db, user["tenant_id"], source.tool_id):
        raise HTTPException(409, "Remove agent, skill and workflow references to the generated tool first")
    await db.execute(delete(BusinessRecord).where(
        BusinessRecord.source_id == source.id, BusinessRecord.tenant_id == user["tenant_id"],
    ))
    tool_id = source.tool_id
    await db.delete(source)
    await db.flush()
    await db.execute(delete(ToolDefinition).where(
        ToolDefinition.id == tool_id, ToolDefinition.tenant_id == user["tenant_id"],
    ))


@router.get("/{source_id}/records")
async def list_records(
    source_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    await _table(db, source_id, tenant_id)
    predicates = (BusinessRecord.source_id == source_id, BusinessRecord.tenant_id == tenant_id)
    total = await db.scalar(select(func.count()).select_from(BusinessRecord).where(*predicates))
    records = await db.scalars(select(BusinessRecord).where(*predicates).order_by(
        BusinessRecord.created_at, BusinessRecord.id,
    ).offset(offset).limit(limit))
    return {"items": [RecordOut.model_validate(record) for record in records],
            "total": total, "offset": offset, "limit": limit}


@router.post("/{source_id}/records", response_model=RecordOut, status_code=201)
async def create_record(
    source_id: str, body: RecordWrite, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(_source_write),
):
    source = await _table(db, source_id, tenant_id)
    record = BusinessRecord(source_id=source_id, tenant_id=tenant_id, values=validate_record(source.fields, body.values))
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def _record(db: AsyncSession, source_id: str, record_id: str, tenant_id: str) -> BusinessRecord:
    record = await db.scalar(select(BusinessRecord).where(
        BusinessRecord.id == record_id, BusinessRecord.source_id == source_id, BusinessRecord.tenant_id == tenant_id,
    ))
    if record is None:
        raise HTTPException(404, "Record not found")
    return record


@router.put("/{source_id}/records/{record_id}", response_model=RecordOut)
async def update_record(
    source_id: str, record_id: str, body: RecordWrite,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(_source_write),
):
    source = await _table(db, source_id, tenant_id)
    record = await _record(db, source_id, record_id, tenant_id)
    record.values = validate_record(source.fields, body.values)
    await db.flush()
    await db.refresh(record)
    return record


@router.delete("/{source_id}/records/{record_id}", status_code=204)
async def delete_record(
    source_id: str, record_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(_source_write),
):
    await _table(db, source_id, tenant_id)
    await db.delete(await _record(db, source_id, record_id, tenant_id))


async def _insert_records(db: AsyncSession, source: BusinessDataSource, records: list[dict]) -> dict:
    db.add_all([BusinessRecord(source_id=source.id, tenant_id=source.tenant_id, values=row) for row in records])
    await db.flush()
    return {"imported": len(records)}


@router.post("/{source_id}/records/import", status_code=201)
async def import_records(
    source_id: str, body: RecordImport, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(_source_write),
):
    source = await _table(db, source_id, tenant_id)
    records = validate_import(source.fields, body.records)
    return await _insert_records(db, source, records)


@router.post("/{source_id}/records/import-csv", status_code=201)
async def import_csv(
    source_id: str, file: UploadFile = File(...), tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(_source_write),
):
    source = await _table(db, source_id, tenant_id)
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    records = parse_csv(source.fields, raw)
    return await _insert_records(db, source, records)


@router.post("/{source_id}/query")
async def query_source(
    source_id: str, body: DataQuery, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    return await execute_data_query(db, source_id, tenant_id, body.model_dump(exclude_none=True))
