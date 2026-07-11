"""EventSubscription model — outbound webhook subscriptions for platform events.

A tenant registers a URL + shared secret and a list of event types it wants
to receive (or ["*"] for everything). See server/engine/event_dispatcher.py
for the delivery mechanism (HMAC-signed POST with retry).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from server.db import Base


class EventSubscription(Base):
    """A tenant-scoped outbound webhook subscription."""

    __tablename__ = "event_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), default="default", index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    secret: Mapped[str] = mapped_column(String(256), nullable=False)
    # List of event type strings this subscription wants, or ["*"] for all.
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
