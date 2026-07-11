"""Event subscription management API — tenant-scoped CRUD.

Subscriptions register a URL + shared secret and a list of event types
(or `["*"]` for everything). Delivery (HMAC-signed POST with retry) is
handled by server/engine/event_dispatcher.py, wired into emission points in
server/engine/workflow_executor.py.

The `secret` is write-only: it is accepted on create/update but never
included in any response body, matching the "no sensitive data in
responses" convention used for API keys elsewhere in this codebase.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.middleware.auth import get_current_user, get_tenant_id
from server.models.subscription import EventSubscription
from server.engine.event_dispatcher import check_webhook_url, WebhookTargetBlockedError

router = APIRouter(dependencies=[Depends(get_current_user)])


class EventSubscriptionCreate(BaseModel):
    name: str
    url: str
    secret: str
    events: list[str] = Field(default_factory=list)
    enabled: bool = True


class EventSubscriptionUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    secret: str | None = None
    events: list[str] | None = None
    enabled: bool | None = None


class EventSubscriptionOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    url: str
    events: list[str]
    enabled: bool
    created_at: str | None = None


def _to_out(sub: EventSubscription) -> EventSubscriptionOut:
    return EventSubscriptionOut(
        id=sub.id,
        tenant_id=sub.tenant_id,
        name=sub.name,
        url=sub.url,
        events=list(sub.events or []),
        enabled=sub.enabled,
        created_at=sub.created_at.isoformat() if sub.created_at else None,
    )


async def _validate_url(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must start with http:// or https://")
    # Reject SSRF targets (loopback/private/link-local/metadata) at create time.
    # check_webhook_url does a blocking DNS lookup (socket.getaddrinfo), so run
    # it off the event loop thread.
    try:
        await asyncio.to_thread(check_webhook_url, url)
    except WebhookTargetBlockedError as e:
        raise HTTPException(400, str(e))


async def _get_owned_subscription(
    db: AsyncSession, subscription_id: str, tenant_id: str,
) -> EventSubscription:
    result = await db.execute(
        select(EventSubscription).where(
            EventSubscription.id == subscription_id,
            EventSubscription.tenant_id == tenant_id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Subscription not found")
    return sub


@router.get("/", response_model=list[EventSubscriptionOut])
async def list_subscriptions(
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EventSubscription).where(EventSubscription.tenant_id == tenant_id)
    )
    return [_to_out(s) for s in result.scalars().all()]


@router.post("/", response_model=EventSubscriptionOut, status_code=201)
async def create_subscription(
    body: EventSubscriptionCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    await _validate_url(body.url)

    sub = EventSubscription(
        tenant_id=tenant_id,
        name=body.name,
        url=body.url,
        secret=body.secret,
        events=body.events,
        enabled=body.enabled,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return _to_out(sub)


@router.get("/{subscription_id}", response_model=EventSubscriptionOut)
async def get_subscription(
    subscription_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    sub = await _get_owned_subscription(db, subscription_id, tenant_id)
    return _to_out(sub)


@router.put("/{subscription_id}", response_model=EventSubscriptionOut)
async def update_subscription(
    subscription_id: str,
    body: EventSubscriptionUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    sub = await _get_owned_subscription(db, subscription_id, tenant_id)

    update_data = body.model_dump(exclude_unset=True)
    if "url" in update_data:
        await _validate_url(update_data["url"])

    for key, value in update_data.items():
        setattr(sub, key, value)

    await db.commit()
    await db.refresh(sub)
    return _to_out(sub)


@router.delete("/{subscription_id}", status_code=204)
async def delete_subscription(
    subscription_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    sub = await _get_owned_subscription(db, subscription_id, tenant_id)
    await db.delete(sub)
    await db.commit()
