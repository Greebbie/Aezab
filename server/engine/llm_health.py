"""LLM reachability check for `/health` (`?check_llm=true`).

Resolves whatever LLM configuration is currently in effect — the tenant's
default `LLMConfig` row in the DB, falling back to the process-wide
`settings.llm_*` env vars when no default config exists — and sends one
minimal chat request through `server.engine.llm_adapter.LLMAdapter` to
classify reachability into a status a non-technical user can act on.

Cost control: `/health` may be polled by monitoring systems at high
frequency, and every call to `check_llm_health()` costs a real LLM request.
Results are cached at module level for `_CACHE_TTL_SECONDS`; `force=True`
(wired from `/health?check_llm=true&force=true`, e.g. a user-triggered
"recheck" button) bypasses the cache. `server/main.py`'s `/health` handler
only calls this module at all when `check_llm=true` is passed — the default
(unauthenticated, frequently-polled) path never touches the LLM.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select

from server.config import settings
from server.db import async_session
from server.engine.llm_adapter import LLMAdapter, LLMMessage
from server.exceptions import LLMError, LLMRateLimitError, LLMTimeoutError
from server.models.llm_config import LLMConfig

logger = logging.getLogger(__name__)

# Kept short and cheap on purpose — this call exists to prove reachability,
# not to exercise the model.
_HEALTH_CHECK_MAX_TOKENS = 8
_HEALTH_CHECK_TIMEOUT_SECONDS = 10
_CACHE_TTL_SECONDS = 60.0

_FRIENDLY_MESSAGES: dict[str, str] = {
    "healthy": "LLM 连接正常。",
    "auth_error": "API Key 无效或已过期，请到「模型配置」页重新测试连接。",
    "rate_limited": "调用频率受限或账户余额不足，请检查服务商账户余额或稍后重试。",
    "unreachable": "无法连接到 LLM 服务地址，请检查网络连接或 Base URL 是否正确。",
    "not_configured": "尚未配置默认 LLM，请到「模型配置」页添加一个配置并设为默认。",
    "error": "LLM 调用失败，请到「模型配置」页测试连接查看详情。",
}

# Cache state — a single shared result since `/health` checks the
# process-wide "currently effective" LLM, not a per-tenant/per-agent one.
_cache: dict[str, Any] | None = None
_cache_checked_at: float = 0.0
_cache_lock = asyncio.Lock()


def _build_result(status: str, model: str | None, base_url: str | None, message: str) -> dict[str, Any]:
    """Assemble the /health `components.llm` payload. Never includes the API
    key — callers must be able to log/display this safely.

    `message_key` is always one of `_FRIENDLY_MESSAGES`'s keys (`status`
    itself doubles as the key — every status value this module produces has
    a matching friendly-message entry). The console i18n-renders
    `health.llm.<message_key>` and falls back to the Chinese `message` string
    below only when that translation key is missing, so English-UI users no
    longer see backend-generated Chinese text mixed into an otherwise
    English health card.
    """
    return {
        "status": status,
        "model": model,
        "base_url": base_url,
        "message": message,
        "message_key": status,
    }


async def _resolve_llm_target() -> tuple[str, str, str] | None:
    """Return (base_url, api_key, model) for the currently-effective LLM
    config: tenant "default"'s `is_default` LLMConfig row first, else the
    process-wide `settings.llm_*` fallback. Returns None only when neither
    source has a base_url configured at all (`not_configured`)."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(LLMConfig).where(
                    LLMConfig.tenant_id == "default",
                    LLMConfig.is_default.is_(True),
                )
            )
            config = result.scalar_one_or_none()
            if config is not None:
                # api_key is stored encrypted at rest (enc:v1: prefix) —
                # decrypt before handing it to the adapter as a Bearer token.
                from server.engine.secrets_store import decrypt_secret

                return config.base_url, decrypt_secret(config.api_key), config.model
    except Exception as e:  # noqa: BLE001 - DB unavailable must not crash /health
        logger.warning("llm_health: failed to load default LLMConfig from DB: %s", e)

    if not settings.llm_base_url:
        return None
    return settings.llm_base_url, settings.llm_api_key, settings.llm_model


def _classify_exception(exc: Exception) -> tuple[str, str]:
    """Map an LLMAdapter.chat() failure to a (status, friendly_message) pair.

    LLMAdapter._to_llm_error normalizes every failure into an LLMError
    subclass (see server/engine/llm_adapter.py:~214-230): httpx timeouts
    become LLMTimeoutError, HTTP 429 becomes LLMRateLimitError, everything
    else (including HTTP 401/403 and connection-level failures like DNS/
    refused-connection) becomes a plain LLMError whose `.message` /
    `.detail` we inspect below since the adapter doesn't classify those
    further itself.
    """
    if isinstance(exc, LLMTimeoutError):
        return "unreachable", _FRIENDLY_MESSAGES["unreachable"]
    if isinstance(exc, LLMRateLimitError):
        return "rate_limited", _FRIENDLY_MESSAGES["rate_limited"]
    if isinstance(exc, LLMError):
        detail = exc.detail or {}
        status_code = detail.get("status_code")
        text = f"{exc.message} {detail.get('body', '')}".lower()
        auth_markers = ("unauthorized", "invalid api key", "invalid_api_key", "authentication", "api key", "forbidden")
        connect_markers = ("connect", "getaddrinfo", "name or service not known", "ssl", "certificate", "dns", "refused")
        if status_code in (401, 403) or any(m in text for m in auth_markers):
            return "auth_error", _FRIENDLY_MESSAGES["auth_error"]
        if any(m in text for m in connect_markers):
            return "unreachable", _FRIENDLY_MESSAGES["unreachable"]
        return "error", f"{_FRIENDLY_MESSAGES['error']}（{exc.message}）"
    return "error", f"{_FRIENDLY_MESSAGES['error']}（{exc}）"


async def _run_check() -> dict[str, Any]:
    """Actually resolve the config and make the one real LLM call.
    Never raises — callers (and /health) get a result dict even when the
    check itself blows up unexpectedly."""
    try:
        target = await _resolve_llm_target()
    except Exception as e:  # noqa: BLE001 - resolution must never crash /health
        logger.warning("llm_health: target resolution failed: %s", e)
        return _build_result("error", None, None, f"{_FRIENDLY_MESSAGES['error']}（{e}）")

    if target is None:
        return _build_result("not_configured", None, None, _FRIENDLY_MESSAGES["not_configured"])

    base_url, api_key, model = target
    adapter = LLMAdapter(
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_tokens=_HEALTH_CHECK_MAX_TOKENS,
        timeout=_HEALTH_CHECK_TIMEOUT_SECONDS,
    )
    try:
        await adapter.chat([LLMMessage(role="user", content="ping")])
    except Exception as exc:  # noqa: BLE001 - classified below, never re-raised
        status, message = _classify_exception(exc)
        return _build_result(status, model, base_url, message)

    return _build_result("healthy", model, base_url, _FRIENDLY_MESSAGES["healthy"])


async def check_llm_health(force: bool = False) -> dict[str, Any]:
    """Return the current LLM health status, using a 60s module-level cache
    unless `force=True`. Never raises.

    The cache is guarded by an asyncio.Lock so concurrent callers within the
    same TTL window (e.g. several /health polls arriving close together)
    share one real LLM call instead of each firing their own.
    """
    global _cache, _cache_checked_at

    now = time.monotonic()
    if not force and _cache is not None and (now - _cache_checked_at) < _CACHE_TTL_SECONDS:
        return _cache

    async with _cache_lock:
        # Re-check inside the lock: another caller may have just refreshed
        # the cache while we were waiting for it.
        now = time.monotonic()
        if not force and _cache is not None and (now - _cache_checked_at) < _CACHE_TTL_SECONDS:
            return _cache

        result = await _run_check()
        _cache = result
        _cache_checked_at = time.monotonic()
        return result
