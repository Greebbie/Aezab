"""Typed equality queries over owned records or an operator-configured PostgreSQL relation."""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import asyncpg
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.engine.secrets_store import decrypt_secret
from server.models.business_data import BusinessDataSource, BusinessRecord

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 1000
MAX_STRING_LENGTH = 10000


class BusinessDataError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def validate_configuration(config: dict[str, Any], *, has_credentials: bool) -> None:
    names = [field["name"] for field in config["fields"]]
    if len(names) != len(set(names)):
        raise BusinessDataError("Field names must be unique")
    filters = config["filter_fields"]
    required = config["required_filters"]
    if len(filters) != len(set(filters)) or not set(filters).issubset(names):
        raise BusinessDataError("Filter fields must be unique declared field names")
    if len(required) != len(set(required)) or not set(required).issubset(filters):
        raise BusinessDataError("Required filters must be selected filter fields")
    if config["kind"] == "postgres":
        if not has_credentials or not config.get("postgres_table"):
            raise BusinessDataError("PostgreSQL sources require credentials and a table or view")
        if config.get("tenant_column") in filters:
            raise BusinessDataError("The tenant column is server-owned and cannot be a user filter")
    elif has_credentials or config.get("postgres_table") or config.get("tenant_column"):
        raise BusinessDataError("Table sources do not accept PostgreSQL connection settings")


def validate_dsn(value: str) -> None:
    # Validation errors intentionally never include the supplied DSN.
    try:
        parsed = urlparse(value)
        valid = parsed.scheme in {"postgres", "postgresql"} and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid or len(value) > 4096:
        raise BusinessDataError("Provide a valid PostgreSQL connection URI")


def tool_input_schema(source: BusinessDataSource) -> dict[str, Any]:
    properties = {}
    for field in source.fields:
        if field["name"] not in source.filter_fields:
            continue
        field_type = field["type"]
        definition: dict[str, Any] = {"type": "string" if field_type == "date" else field_type}
        if field_type == "date":
            definition["format"] = "date"
        if field_type == "string":
            definition["maxLength"] = MAX_STRING_LENGTH
        properties[field["name"]] = definition
    return {
        "type": "object", "additionalProperties": False, "required": ["filters"],
        "properties": {
            "filters": {"type": "object", "additionalProperties": False,
                        "properties": properties, "required": list(source.required_filters)},
            "limit": {"type": "integer", "minimum": 1, "maximum": source.max_rows},
        },
    }


def _value(value: Any, field: dict, *, csv_input: bool = False) -> Any:
    name, kind = field["name"], field["type"]
    required = field.get("required", True)
    if csv_input and value == "":
        value = None
    if value is None:
        if required:
            raise BusinessDataError(f"Field '{name}' is required")
        return None
    try:
        if csv_input:
            if kind == "integer":
                value = int(value)
            elif kind == "number":
                value = float(value)
            elif kind == "boolean":
                if value.lower() not in {"true", "false"}:
                    raise ValueError
                value = value.lower() == "true"
        if kind == "string":
            valid = isinstance(value, str) and len(value) <= MAX_STRING_LENGTH
            valid = valid and (bool(value) or not required)
        elif kind == "integer":
            valid = isinstance(value, int) and not isinstance(value, bool) and -(2**63) <= value < 2**63
        elif kind == "number":
            valid = isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
            if valid:
                value = float(value)
                valid = math.isfinite(value)
        elif kind == "boolean":
            valid = isinstance(value, bool)
        elif kind == "date":
            if type(value) is date:
                value = value.isoformat()
            valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
        else:
            valid = False
    except (ValueError, TypeError, OverflowError):
        valid = False
    if not valid:
        raise BusinessDataError(f"Field '{name}' must be a valid {kind}")
    return value


def validate_record(fields: list[dict], values: dict[str, Any], *, csv_input: bool = False) -> dict:
    names = {field["name"] for field in fields}
    if set(values) - names:
        raise BusinessDataError("Record contains undeclared fields")
    return {field["name"]: _value(values.get(field["name"]), field, csv_input=csv_input) for field in fields}


def validate_import(fields: list[dict], records: list[dict], *, csv_input: bool = False) -> list[dict]:
    if not 1 <= len(records) <= MAX_IMPORT_ROWS:
        raise BusinessDataError(f"Import requires 1 to {MAX_IMPORT_ROWS} records")
    try:
        byte_size = len(json.dumps(records, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError):
        raise BusinessDataError("Import contains invalid JSON values") from None
    if byte_size > MAX_IMPORT_BYTES:
        raise BusinessDataError("Import exceeds the 2 MB size limit", 413)
    normalized = []
    for index, record in enumerate(records, 1):
        try:
            normalized.append(validate_record(fields, record, csv_input=csv_input))
        except BusinessDataError as exc:
            raise BusinessDataError(f"Row {index}: {exc}") from None
    return normalized


def parse_csv(fields: list[dict], raw: bytes) -> list[dict]:
    if len(raw) > MAX_IMPORT_BYTES:
        raise BusinessDataError("CSV exceeds the 2 MB size limit", 413)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        headers = reader.fieldnames or []
        names = {field["name"] for field in fields}
        if not headers or len(headers) != len(set(headers)) or not set(headers).issubset(names):
            raise BusinessDataError("CSV headers must be unique declared field names")
        records = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise BusinessDataError("CSV rows must match the header column count")
            records.append(row)
            if len(records) > MAX_IMPORT_ROWS:
                raise BusinessDataError(f"CSV exceeds the {MAX_IMPORT_ROWS} row limit")
    except (UnicodeDecodeError, csv.Error):
        raise BusinessDataError("Upload a valid UTF-8 CSV file") from None
    return validate_import(fields, records, csv_input=True)


def _query_input(source: BusinessDataSource, input_data: dict[str, Any]) -> tuple[dict, int]:
    if not isinstance(input_data, dict) or set(input_data) - {"filters", "limit"}:
        raise BusinessDataError("Queries accept only filters and limit")
    filters = input_data.get("filters", {})
    if not isinstance(filters, dict) or set(filters) - set(source.filter_fields):
        raise BusinessDataError("Query contains an unapproved filter field")
    if any(name not in filters or filters[name] is None for name in source.required_filters):
        raise BusinessDataError("Query is missing a required filter")
    fields = {field["name"]: field for field in source.fields}
    normalized = {name: _value(value, fields[name]) for name, value in filters.items()}
    limit = input_data.get("limit", source.max_rows)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= source.max_rows:
        raise BusinessDataError(f"Query limit must be an integer from 1 to {source.max_rows}")
    return normalized, limit


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


async def _postgres_rows(source: BusinessDataSource, tenant_id: str, filters: dict, limit: int) -> list[dict]:
    fields = {field["name"]: field for field in source.fields}
    clauses, parameters = [], []
    for name, value in filters.items():
        if value is None:
            clauses.append(f"{_quote(name)} IS NULL")
        else:
            if fields[name]["type"] == "date":
                value = date.fromisoformat(value)
            parameters.append(value)
            clauses.append(f"{_quote(name)} = ${len(parameters)}")
    if source.tenant_column:
        parameters.append(tenant_id)
        clauses.append(f"{_quote(source.tenant_column)} = ${len(parameters)}")
    relation = f"{_quote(source.postgres_schema or 'public')}.{_quote(source.postgres_table)}"
    sql = f"SELECT {', '.join(_quote(name) for name in fields)} FROM {relation}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    parameters.append(limit + 1)
    sql += f" LIMIT ${len(parameters)}"
    connection = None
    try:
        dsn = decrypt_secret(source.postgres_dsn_encrypted or "")
        if not dsn:
            raise BusinessDataError("PostgreSQL credentials are unavailable", 502)
        connection = await asyncpg.connect(dsn, timeout=5, command_timeout=5)
        async with connection.transaction(readonly=True):
            await connection.execute("SET LOCAL statement_timeout = '5000ms'")
            records = await connection.fetch(sql, *parameters)
            return [validate_record(source.fields, dict(row)) for row in records]
    except Exception:
        # Provider/driver messages can contain DSNs, usernames, SQL or data.
        raise BusinessDataError("PostgreSQL query failed; verify connection, table and field configuration", 502) from None
    finally:
        if connection is not None:
            try:
                await connection.close(timeout=2)
            except Exception:
                connection.terminate()


async def execute_data_query(
    db: AsyncSession, source_id: str, tenant_id: str, input_data: dict[str, Any],
) -> dict[str, Any]:
    """tenant_id must come from the authorized tool, never model/user arguments."""
    source = await db.scalar(select(BusinessDataSource).where(
        BusinessDataSource.id == source_id, BusinessDataSource.tenant_id == tenant_id,
    ))
    if source is None or not source.enabled:
        raise BusinessDataError("Data source not found or disabled", 404)
    filters, limit = _query_input(source, input_data)
    if source.kind == "postgres":
        rows = await _postgres_rows(source, tenant_id, filters, limit)
    else:
        query = select(BusinessRecord).where(
            BusinessRecord.source_id == source.id, BusinessRecord.tenant_id == tenant_id,
        )
        fields = {field["name"]: field for field in source.fields}
        for name, value in filters.items():
            column = BusinessRecord.values[name]
            kind = fields[name]["type"]
            if kind == "integer":
                column = column.as_integer()
            elif kind == "number":
                column = column.as_float()
            elif kind == "boolean":
                column = column.as_boolean()
            else:
                column = column.as_string()
            query = query.where(column == value)
        result = await db.scalars(query.order_by(BusinessRecord.created_at, BusinessRecord.id).limit(limit + 1))
        rows = [record.values for record in result.all()]
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {
        "source_id": source.id,
        "columns": [{"name": field["name"], "type": field["type"]} for field in source.fields],
        "rows": rows, "row_count": len(rows), "truncated": truncated,
    }
