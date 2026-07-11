"""Authentication API endpoints.

Provides:
- GET  /auth/status      — public bootstrap check for the console's AuthGate
- POST /auth/login       — authenticate with username/password, receive JWT
- POST /auth/register    — create new user (admin-only, or first user auto-admin)
- GET  /auth/me          — get current user info
- POST /auth/api-keys    — create a new API key (raw key shown only once)
- GET  /auth/api-keys    — list API keys (without raw key values)
- DELETE /auth/api-keys/{key_id} — revoke an API key
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.db import get_db
from server.middleware.auth import (
    create_jwt_token,
    generate_api_key,
    get_current_user,
    hash_api_key,
    hash_password,
    require_role,
    security,
    verify_password,
)
from server.models.user import APIKey as APIKeyModel
from server.models.user import User
from server.schemas.auth import (
    APIKeyCreatedResponse,
    APIKeyOut,
    CreateAPIKeyRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserInfo,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status")
async def auth_status(db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    """Public bootstrap endpoint used by the console's AuthGate.

    Tells the frontend, before any credentials exist, whether it should
    render the app directly (auth disabled), show a first-run "create
    admin account" screen (no users yet), or show the login screen.
    Intentionally returns only two booleans — no user data, counts, or
    other details that could help an attacker profile the deployment.
    """
    if settings.disable_auth:
        return {"auth_disabled": True, "needs_setup": False}

    count_result = await db.execute(select(func.count(User.id)))
    user_count = count_result.scalar() or 0

    return {"auth_disabled": False, "needs_setup": user_count == 0}


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate with username and password. Returns a JWT token."""
    result = await db.execute(
        select(User).where(User.username == body.username),
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not user.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    token = create_jwt_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
    )

    logger.info("User '%s' logged in successfully", user.username)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in_minutes=settings.jwt_expire_minutes,
        user=UserInfo(
            id=user.id,
            username=user.username,
            role=user.role,
            tenant_id=user.tenant_id,
            display_name=user.display_name,
            enabled=user.enabled,
            created_at=user.created_at,
        ),
    )


@router.post("/register", response_model=UserInfo, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Create a new user account.

    Rules:
    - If no users exist yet, the first user is auto-promoted to admin (no auth needed).
    - Otherwise, only authenticated admins can create new users.
    - In dev mode (disable_auth=True), registration is always allowed.
    """
    # Count existing users to determine if this is the first user
    count_result = await db.execute(select(func.count(User.id)))
    user_count = count_result.scalar() or 0

    is_first_user = user_count == 0

    if not is_first_user and not settings.disable_auth:
        # Require admin authentication for subsequent registrations
        try:
            current_user = await get_current_user(request, credentials, db)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only authenticated admins can register new users.",
            )
        if current_user.get("role") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins can register new users.",
            )

    # Check for duplicate username
    existing = await db.execute(
        select(User).where(User.username == body.username),
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken",
        )

    # First user is always admin
    role = "admin" if is_first_user else body.role

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=role,
        tenant_id=body.tenant_id,
        display_name=body.display_name or body.username,
    )
    db.add(user)
    await db.flush()

    logger.info(
        "User '%s' registered with role '%s' (first_user=%s)",
        user.username,
        role,
        is_first_user,
    )

    return UserInfo(
        id=user.id,
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        display_name=user.display_name,
        enabled=user.enabled,
        created_at=user.created_at,
    )


@router.post(
    "/register-admin",
    response_model=UserInfo,
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
)
async def register_by_admin(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user account (admin-only endpoint).

    This endpoint requires admin authentication and allows creating
    users with any role.
    """
    # Check for duplicate username
    existing = await db.execute(
        select(User).where(User.username == body.username),
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken",
        )

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        tenant_id=body.tenant_id,
        display_name=body.display_name or body.username,
    )
    db.add(user)
    await db.flush()

    logger.info("Admin created user '%s' with role '%s'", user.username, user.role)

    return UserInfo(
        id=user.id,
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        display_name=user.display_name,
        enabled=user.enabled,
        created_at=user.created_at,
    )


@router.get("/me", response_model=UserInfo)
async def get_me(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current authenticated user's info."""
    # In dev mode, return mock user info directly
    if settings.disable_auth:
        return UserInfo(
            id=current_user["id"],
            username=current_user["username"],
            role=current_user["role"],
            tenant_id=current_user["tenant_id"],
            display_name=current_user.get("display_name", ""),
            enabled=True,
        )

    # Look up fresh data from DB
    result = await db.execute(
        select(User).where(User.id == current_user["id"]),
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserInfo(
        id=user.id,
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        display_name=user.display_name,
        enabled=user.enabled,
        created_at=user.created_at,
    )


@router.post("/api-keys", response_model=APIKeyCreatedResponse, status_code=201)
async def create_api_key(
    body: CreateAPIKeyRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new API key for the current user.

    The raw key is returned only once in the response.
    Store it securely — it cannot be retrieved again.
    """
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)

    api_key = APIKeyModel(
        user_id=current_user["id"],
        key_hash=key_hash,
        name=body.name,
        tenant_id=current_user.get("tenant_id", "default"),
        scopes=body.scopes,
    )
    db.add(api_key)
    await db.flush()

    logger.info(
        "API key '%s' created for user '%s'",
        body.name or api_key.id,
        current_user["username"],
    )

    return APIKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        key=raw_key,
        tenant_id=api_key.tenant_id,
        scopes=api_key.scopes,
    )


@router.get("/api-keys", response_model=list[APIKeyOut])
async def list_api_keys(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all API keys for the current user (without raw key values)."""
    result = await db.execute(
        select(APIKeyModel).where(APIKeyModel.user_id == current_user["id"]),
    )
    keys = result.scalars().all()

    return [
        APIKeyOut(
            id=k.id,
            name=k.name,
            tenant_id=k.tenant_id,
            scopes=k.scopes,
            enabled=k.enabled,
            last_used_at=k.last_used_at,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke (disable) an API key.

    Users can only revoke their own keys. Admins can revoke any key.
    """
    result = await db.execute(
        select(APIKeyModel).where(APIKeyModel.id == key_id),
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    # Non-admins can only revoke their own keys
    if (
        current_user.get("role") != "admin"
        and api_key.user_id != current_user["id"]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot revoke another user's API key",
        )

    api_key.enabled = False
    logger.info(
        "API key '%s' revoked by user '%s'",
        key_id,
        current_user["username"],
    )
