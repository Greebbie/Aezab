"""Workflow & WorkflowStep — declarative process orchestration."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db import Base


class Workflow(Base):
    """A configurable business process (e.g. '办理居住证')."""

    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    tenant_id: Mapped[str] = mapped_column(String(36), default="default")
    version: Mapped[int] = mapped_column(default=1)
    # Global workflow config: timeout, max_retries, etc.
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    steps: Mapped[list[WorkflowStep]] = relationship(
        "WorkflowStep", back_populates="workflow", order_by="WorkflowStep.order"
    )


class WorkflowStep(Base):
    """A single step in a workflow."""

    __tablename__ = "workflow_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id: Mapped[str] = mapped_column(String(36), ForeignKey("workflows.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(
        String(32), default="collect"
    )  # collect | validate | tool_call | decision | confirm | human_review | complete

    # Prompt template shown to user at this step
    prompt_template: Mapped[str] = mapped_column(Text, default="")

    # Form fields definition (JSON array)
    # Each field: {name, label, type, required, validation_rule, placeholder, options}
    fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Validation rules (JSON)
    validation_rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Tool to invoke at this step (tool_id)
    tool_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Tool call config: input mapping, output mapping
    tool_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Failure / fallback config
    on_failure: Mapped[str] = mapped_column(
        String(32), default="retry"
    )  # retry | skip | rollback | escalate
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    fallback_step_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Human-in-the-loop: whether this step requires human confirmation
    requires_human_confirm: Mapped[bool] = mapped_column(default=False)
    # Risk level (info / warning / critical)
    risk_level: Mapped[str] = mapped_column(String(16), default="info")

    # Next step logic: simple linear or conditional
    next_step_rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    workflow: Mapped[Workflow] = relationship("Workflow", back_populates="steps")


class WorkflowVersion(Base):
    """Snapshot of a workflow definition at a point in time."""

    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_wf_ver"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Full snapshot of workflow + steps as JSON
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Who published this version
    published_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
