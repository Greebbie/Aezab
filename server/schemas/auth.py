"""Schemas for authentication endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# bcrypt hashes only the first 72 bytes of a password (a hard library
# limit). `hash_password()` in server/middleware/auth.py raises ValueError
# for anything longer rather than silently truncating it. Validating the
# byte length here means an over-long password is rejected with a 422 at
# the schema boundary instead of surfacing as a 500 from hash_password().
_PASSWORD_MIN_LENGTH = 8
_PASSWORD_MAX_BYTES = 72


def _validate_password_bytes(value: str) -> str:
    byte_length = len(value.encode("utf-8"))
    if byte_length > _PASSWORD_MAX_BYTES:
        raise ValueError(
            f"密码长度需在 {_PASSWORD_MIN_LENGTH}-{_PASSWORD_MAX_BYTES} 字符之间 "
            f"(password must be {_PASSWORD_MIN_LENGTH}-{_PASSWORD_MAX_BYTES} bytes when UTF-8 encoded, "
            f"got {byte_length} bytes)"
        )
    return value


# ── Request schemas ─────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1)

    @field_validator("password")
    @classmethod
    def _password_max_bytes(cls, value: str) -> str:
        # Login only needs the upper bound enforced (min_length=1 already
        # rejects empty passwords; existing users may have legacy hashes
        # so we don't enforce the 8-char minimum here).
        return _validate_password_bytes(value)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=128)
    password: str = Field(..., min_length=_PASSWORD_MIN_LENGTH, max_length=256)
    role: str = Field(default="editor", pattern="^(admin|editor|viewer)$")
    tenant_id: str = Field(default="default", max_length=36)
    display_name: str = Field(default="", max_length=128)

    @field_validator("password")
    @classmethod
    def _password_byte_length(cls, value: str) -> str:
        return _validate_password_bytes(value)


class CreateAPIKeyRequest(BaseModel):
    name: str = Field(default="", max_length=128)
    scopes: list[str] | None = None


# ── Response schemas ────────────────────────────────────────────


class UserInfo(BaseModel):
    id: str
    username: str
    role: str
    tenant_id: str
    display_name: str
    enabled: bool
    created_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: UserInfo


class APIKeyOut(BaseModel):
    id: str
    name: str
    tenant_id: str
    scopes: list[str] | None = None
    enabled: bool
    last_used_at: datetime | None = None
    created_at: datetime | None = None


class APIKeyCreatedResponse(BaseModel):
    """Returned only once when an API key is created. The raw key is never shown again."""

    id: str
    name: str
    key: str  # Raw key — shown only once
    tenant_id: str
    scopes: list[str] | None = None
