"""At-rest encryption for provider secrets stored in the database.

`llm_configs.api_key` used to be stored in plaintext, which meant every
daily backup zip (see `server/engine/backup.py`) carried every tenant's LLM
provider credentials out of the database in the clear. This module wraps
those values with Fernet (AES-128-CBC + HMAC, authenticated) symmetric
encryption keyed off `settings.secret_key`.

Key derivation: Fernet requires a 32-byte urlsafe-base64 key, but
`settings.secret_key` is an arbitrary-length random token (see
`server.config._ensure_persistent_secret_key`), so it is hashed down to a
fixed 32-byte digest via SHA-256 before being base64-encoded. This module
never touches `settings.secret_key` generation/persistence itself.

Encrypted values are tagged with a `"enc:v1:"` prefix so `decrypt_secret`
can distinguish already-encrypted values from legacy plaintext still
sitting in the database (backward compatible with rows written before this
module existed, and with the one-time migration in
`migrate_plaintext_llm_keys` below).
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:v1:"


def _fernet_key() -> bytes:
    """Derive a 32-byte urlsafe-base64 Fernet key from `settings.secret_key`.

    Deterministic: the same `secret_key` always derives the same Fernet key,
    so encryption/decryption stay consistent across process restarts as long
    as `secret_key` itself is stable (it is persisted to `./data/secret_key`
    by `_ensure_persistent_secret_key`).
    """
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_fernet_key())


def is_encrypted(value: str) -> bool:
    """Return True if `value` already carries the `enc:v1:` marker."""
    return value.startswith(_ENC_PREFIX)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt `plaintext`, returning `"enc:v1:" + fernet_token`.

    Empty strings are returned unchanged (an unset key stays unset; nothing
    sensitive to protect and it keeps `if not api_key` checks working
    downstream without needing to know about the encryption scheme).
    """
    if not plaintext:
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    """Decrypt a value previously produced by `encrypt_secret`.

    Backward compatibility: a value without the `enc:v1:` prefix is assumed
    to be legacy plaintext (written before this module existed, or before
    `migrate_plaintext_llm_keys` ran) and is returned as-is.

    Failure handling: if `settings.secret_key` was changed/rotated by hand
    (or the stored token is otherwise corrupt), decryption fails. Rather
    than crash the request path, this logs an error once per call and
    returns an empty string — the caller's LLM call then fails cleanly with
    a "key not configured" style error instead of taking down the process.
    This is a deliberate trade-off: losing `secret_key` means every stored
    provider API key becomes unrecoverable and must be re-entered.
    """
    if not value or not is_encrypted(value):
        return value
    token = value[len(_ENC_PREFIX):]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        logger.error(
            "Failed to decrypt stored secret (secret_key rotated or value "
            "corrupt); returning empty string: %s",
            exc,
        )
        return ""


async def migrate_plaintext_llm_keys(session: AsyncSession) -> int:
    """One-time migration: encrypt any `llm_configs.api_key` still stored in
    plaintext. Idempotent — rows already carrying the `enc:v1:` prefix are
    skipped, so this is safe to call on every startup.

    Returns the number of rows that were re-encrypted.
    """
    from server.models.llm_config import LLMConfig

    result = await session.execute(select(LLMConfig))
    configs = result.scalars().all()

    migrated = 0
    for config in configs:
        if not config.api_key or is_encrypted(config.api_key):
            continue
        config.api_key = encrypt_secret(config.api_key)
        migrated += 1

    if migrated:
        await session.commit()
        logger.info("migrate_plaintext_llm_keys: encrypted %d llm_configs.api_key row(s)", migrated)

    return migrated
