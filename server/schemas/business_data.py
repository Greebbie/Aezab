"""Public business-data contracts. PostgreSQL credentials are write-only."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"
FieldType = Literal["string", "integer", "number", "boolean", "date"]


class DataField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=IDENTIFIER_PATTERN)
    type: FieldType
    required: bool = True


class DataSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    kind: Literal["table", "postgres"] = "table"
    fields: list[DataField] = Field(min_length=1, max_length=50)
    filter_fields: list[str] = Field(default_factory=list, max_length=50)
    required_filters: list[str] = Field(default_factory=list, max_length=50)
    max_rows: int = Field(default=20, ge=1, le=200, strict=True)
    postgres_dsn: SecretStr | None = None
    postgres_schema: str = Field(default="public", pattern=IDENTIFIER_PATTERN)
    postgres_table: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    tenant_column: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    enabled: bool = True


class DataSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    fields: list[DataField] | None = Field(default=None, min_length=1, max_length=50)
    filter_fields: list[str] | None = Field(default=None, max_length=50)
    required_filters: list[str] | None = Field(default=None, max_length=50)
    max_rows: int | None = Field(default=None, ge=1, le=200, strict=True)
    postgres_dsn: SecretStr | None = None
    postgres_schema: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    postgres_table: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    tenant_column: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    enabled: bool | None = None


class DataSourceOut(BaseModel):
    id: str
    tool_id: str
    tool_name: str
    tenant_id: str
    name: str
    description: str
    kind: Literal["table", "postgres"]
    fields: list[DataField]
    filter_fields: list[str]
    required_filters: list[str]
    max_rows: int
    postgres_schema: str | None
    postgres_table: str | None
    tenant_column: str | None
    has_credentials: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class RecordWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


class RecordImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    values: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DataQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int | None = Field(default=None, ge=1, le=200, strict=True)
