"""JWT authentication and RBAC middleware.

Provides FastAPI dependencies for:
- JWT token creation and verification
- Password hashing and verification
- API key authentication
- Role-based access control (admin > editor > viewer)

When settings.disable_auth is True, all checks are bypassed
and a mock admin user is returned (development mode).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.db import get_db

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# Role hierarchy: higher number = more privileges
ROLE_HIERARCHY: dict[str, int] = {"admin": 3, "editor": 2, "viewer": 1}

VALID_ROLES = frozenset(ROLE_HIERARCHY.keys())

# ── Optional dependency imports ─────────────────────────────────
# Use try/except so the app doesn't crash if not yet installed.

try:
    import jwt as pyjwt
except ImportError:
    pyjwt = None  # type: ignore[assignment]
    logger.warning("PyJWT not installed — JWT auth will be unavailable")

try:
    from passlib.context import CryptContext

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
except ImportError:
    pwd_context = None  # type: ignore[assignment]
    logger.warning("passlib not installed — password hashing will be unavailable")

# ── Mock user for development mode ──────────────────────────────

_MOCK_USER: dict[str, Any] = {
    "id": "dev-admin-00000000",
    "username": "dev-admin",
    "role": "admin",
    "tenant_id": "default",
    "display_name": "Development Admin",
}


# ── Password utilities ──────────────────────────────────────────


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    if pwd_context is None:
        raise RuntimeError("passlib is not installed — run: pip install passlib[bcrypt]")
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    if pwd_context is None:
        raise RuntimeError("passlib is not installed — run: pip install passlib[bcrypt]")
    return pwd_context.verify(plain_password, hashed_password)


# ── API key utilities ───────────────────────────────────────────


def generate_api_key() -> str:
    """Generate a cryptographically secure API key (48 random bytes, URL-safe)."""
    return secrets.token_urlsafe(48)


def hash_api_key(key: str) -> str:
    """SHA-256 hash an API key for storage. Never store raw keys."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ── JWT utilities ───────────────────────────────────────────────


def create_jwt_token(
    user_id: str,
    tenant_id: str,
    role: str,
    *,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT token for the given user.

    Returns the encoded token string.
    """
    if pyjwt is None:
        raise RuntimeError("PyJWT is not installed — run: pip install PyJWT")

    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    if extra_claims:
        payload.update(extra_claims)

    return pyjwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def verify_jwt_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT token.

    Returns the payload dict on success.
    Raises HTTPException 401 on any failure.
    """
    if pyjwt is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT support not available (PyJWT not installed)",
        )

    try:
        payload = pyjwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        )


# ── FastAPI dependencies ────────────────────────────────────────


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Extract and validate the current user from the request.

    Authentication methods (tried in order):
    1. Bearer JWT token (Authorization header)
    2. API key (X-API-Key header)
    3. If disable_auth is True, return mock admin user

    Returns a dict with: id, username, role, tenant_id, display_name.
    """
    # Development bypass
    if settings.disable_auth:
        return dict(_MOCK_USER)

    # ── Try JWT Bearer token ────────────────────────
    if credentials and credentials.credentials:
        payload = verify_jwt_token(credentials.credentials)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject",
            )

        # Look up the user in the database to verify they still exist and are enabled
        from server.models.user import User

        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        if not user.enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled",
            )

        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "tenant_id": user.tenant_id,
            "display_name": user.display_name,
        }

    # ── Try X-API-Key header ────────────────────────
    api_key_header = request.headers.get("X-API-Key")
    if api_key_header:
        key_hash = hash_api_key(api_key_header)

        from server.models.user import APIKey as APIKeyModel
        from server.models.user import User

        result = await db.execute(
            select(APIKeyModel).where(
                APIKeyModel.key_hash == key_hash,
                APIKeyModel.enabled == True,  # noqa: E712
            ),
        )
        api_key_record = result.scalar_one_or_none()
        if api_key_record is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or disabled API key",
            )

        # Update last_used_at
        api_key_record.last_used_at = datetime.now(timezone.utc)

        # Look up the associated user
        user_result = await db.execute(
            select(User).where(User.id == api_key_record.user_id),
        )
        user = user_result.scalar_one_or_none()
        if user is None or not user.enabled:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key owner not found or disabled",
            )

        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "tenant_id": api_key_record.tenant_id,
            "display_name": user.display_name,
            "api_key_id": api_key_record.id,
            "api_key_scopes": api_key_record.scopes,
        }

    # ── No authentication provided ──────────────────
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide a Bearer token or X-API-Key header.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_role(min_role: str):
    """Dependency factory: require the user to have at least the given role.

    Usage:
        @router.post("/admin-only", dependencies=[Depends(require_role("admin"))])
        async def admin_endpoint(...):
            ...

    Or inject the user:
        async def handler(user=Depends(require_role("editor"))):
            ...
    """
    if min_role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {min_role!r}. Must be one of {VALID_ROLES}")

    required_level = ROLE_HIERARCHY[min_role]

    async def _check_role(
        current_user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        user_role = current_user.get("role", "viewer")
        user_level = ROLE_HIERARCHY.get(user_role, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: {min_role}, your role: {user_role}",
            )
        return current_user

    return _check_role


def get_tenant_id(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> str:
    """Extract tenant_id from the authenticated user.

    This replaces trusting tenant_id from the request body.
    """
    return current_user.get("tenant_id", "default")


# ── API key scope enforcement ───────────────────────────────────
#
# Only API-key-authenticated callers carry "api_key_scopes" (see
# get_current_user's X-API-Key branch above). JWT-authenticated console
# users never carry that key and are therefore never restricted here.


def require_scope(scope: str):
    """Dependency factory: require `scope` among the caller's API key scopes.

    Semantics: if the caller authenticated via API key AND that key's
    `scopes` list is non-empty, `scope` must be present in it or the request
    is rejected with 403. An empty list or None scopes means "unrestricted"
    (backward compatible with API keys created before scopes existed).
    JWT-authenticated console users (no `api_key_scopes` entry at all) are
    unaffected regardless of this dependency.

    Usage — applied at router-include level so no individual API module has
    to be edited:
        app.include_router(agents_router, ..., dependencies=[Depends(require_scope("manage"))])
    """

    async def _check_scope(
        current_user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        scopes = current_user.get("api_key_scopes")
        if scopes and scope not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key is missing required scope: '{scope}'",
            )
        return current_user

    return _check_scope


# ── In-process rate limiting ────────────────────────────────────
#
# Simple sliding-window limiter keyed by api_key_id, falling back to user id,
# falling back to client IP. State lives in this process's memory only — in
# a multi-worker or multi-instance deployment each process enforces its own
# independent limit (no shared/Redis-backed counter). That is an accepted
# tradeoff for this wave; move to Redis if a globally consistent limit is
# ever required.

_RATE_LIMIT_WINDOW_SECONDS = 60.0
_rate_limit_lock = threading.Lock()
_rate_limit_windows: dict[str, deque] = defaultdict(deque)


def _rate_limit_key(request: Request, current_user: dict[str, Any]) -> str:
    api_key_id = current_user.get("api_key_id")
    if api_key_id:
        return f"key:{api_key_id}"
    user_id = current_user.get("id")
    if user_id:
        return f"user:{user_id}"
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


async def enforce_rate_limit(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> None:
    """Per-process sliding-window rate limit, applied only to /invoke routes.

    Wired at router-include level in server/main.py:
        app.include_router(invoke_router, ..., dependencies=[Depends(enforce_rate_limit)])
    """
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return  # 0 or negative disables rate limiting entirely

    key = _rate_limit_key(request, current_user)
    now = time.monotonic()

    with _rate_limit_lock:
        window = _rate_limit_windows[key]
        while window and now - window[0] > _RATE_LIMIT_WINDOW_SECONDS:
            window.popleft()

        # Opportunistic purge: drop other keys whose windows have gone idle,
        # so the dict does not grow unboundedly with one-off callers/IPs.
        stale = [
            k for k, w in _rate_limit_windows.items()
            if k != key and (not w or now - w[-1] > _RATE_LIMIT_WINDOW_SECONDS)
        ]
        for k in stale:
            del _rate_limit_windows[k]

        if len(window) >= limit:
            oldest = window[0]
            retry_after = max(1, int(_RATE_LIMIT_WINDOW_SECONDS - (now - oldest)) + 1)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please slow down.",
                headers={"Retry-After": str(retry_after)},
            )

        window.append(now)
