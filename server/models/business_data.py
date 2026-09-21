"""Tenant-owned structured data and constrained PostgreSQL query sources."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from server.db import Base


class BusinessDataSource(Base):
    __tablename__ = "business_data_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tool_id: Mapped[str] = mapped_column(String(36), ForeignKey("tool_definitions.id"), unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    fields: Mapped[list] = mapped_column(JSON, nullable=False)
    filter_fields: Mapped[list] = mapped_column(JSON, default=list)
    required_filters: Mapped[list] = mapped_column(JSON, default=list)
    max_rows: Mapped[int] = mapped_column(Integer, default=20)
    postgres_dsn_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    postgres_schema: Mapped[str | None] = mapped_column(String(63), nullable=True)
    postgres_table: Mapped[str | None] = mapped_column(String(63), nullable=True)
    tenant_column: Mapped[str | None] = mapped_column(String(63), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BusinessRecord(Base):
    __tablename__ = "business_records"
    __table_args__ = (Index("ix_business_records_tenant_source", "tenant_id", "source_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("business_data_sources.id", ondelete="CASCADE"), nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    values: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
