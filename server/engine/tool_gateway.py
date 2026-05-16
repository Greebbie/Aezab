"""Tool Gateway — unified tool invocation with auth, retry, audit."""

from __future__ import annotations

import logging
import time
import asyncio
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.tool import ToolDefinition
from server.engine.audit_logger import AuditLogger
from server.engine.circuit_breaker import circuit_breaker

logger = logging.getLogger(__name__)


class ToolInvocationError(Exception):
    def __init__(self, tool_name: str, message: str, recoverable: bool = True):
        self.tool_name = tool_name
        self.recoverable = recoverable
        super().__init__(f"Tool '{tool_name}': {message}")


# ── Direct in-process mock tool handlers ─────────────────────────
# These are called directly without HTTP, avoiding loopback issues.

_BUILTIN_TOOL_ALIASES = {
    "calculator": "calculator",
    "calc": "calculator",
    "weather": "weather",
    "unit_converter": "unit_converter",
    "unit": "unit_converter",
    "converter": "unit_converter",
    "timestamp": "timestamp",
    "timestamp_tool": "timestamp",
    "time": "timestamp",
    "create_work_order": "create_work_order",
    "work_order": "create_work_order",
    "webhook": "webhook",
    "webhook_receiver": "webhook",
}


def _normalize_tool_name(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def _resolve_mock_handler(endpoint: str):
    """If the endpoint points to a known mock-tool path, return the handler."""
    if not endpoint:
        return None
    path = urlparse(endpoint).path.rstrip("/")
    return _MOCK_TOOL_HANDLERS.get(path)


def _resolve_builtin_handler(name: str):
    """Resolve a built-in function tool by its saved tool name."""
    builtin_name = _BUILTIN_TOOL_ALIASES.get(_normalize_tool_name(name))
    if not builtin_name:
        return None
    return _MOCK_TOOL_HANDLERS.get(f"/api/v1/mock-tools/{builtin_name}")


def _lazy_init_handlers() -> dict:
    """Lazily import mock tool functions to avoid circular imports."""
    from server.api.mock_tools import (
        calculator,
        create_work_order,
        weather,
        unit_converter,
        timestamp_tool,
        webhook_receiver,
    )
    return {
        "/api/v1/mock-tools/calculator": calculator,
        "/api/v1/mock-tools/weather": weather,
        "/api/v1/mock-tools/unit_converter": unit_converter,
        "/api/v1/mock-tools/timestamp": timestamp_tool,
        "/api/v1/mock-tools/create_work_order": create_work_order,
        "/api/v1/mock-tools/webhook": webhook_receiver,
    }


_MOCK_TOOL_HANDLERS: dict = {}


class ToolGateway:
    """Invoke any registered tool with retry, timeout, auth, and audit."""

    def __init__(self, db: AsyncSession, audit: AuditLogger | None = None):
        self.db = db
        self.audit = audit

    async def get_tool(self, tool_id: str) -> ToolDefinition | None:
        result = await self.db.execute(
            select(ToolDefinition).where(ToolDefinition.id == tool_id, ToolDefinition.enabled.is_(True))
        )
        return result.scalar_one_or_none()

    async def invoke(self, tool_id: str, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke a tool by id. Handles auth, retry, timeout."""
        tool = await self.get_tool(tool_id)
        if tool is None:
            raise ToolInvocationError(tool_id, "Tool not found or disabled", recoverable=False)

        t0 = time.perf_counter()
        last_error: Exception | None = None

        for attempt in range(tool.max_retries + 1):
            try:
                result = await self._call(tool, input_data or {})
                latency = (time.perf_counter() - t0) * 1000

                if self.audit:
                    self.audit.log(
                        "tool_call",
                        tool_meta={
                            "tool_id": tool.id,
                            "tool_name": tool.name,
                            "input": input_data,
                            "output": result,
                            "success": True,
                            "attempt": attempt + 1,
                        },
                        latency_ms=latency,
                    )
                return result

            except Exception as e:
                last_error = e
                if attempt < tool.max_retries:
                    await asyncio.sleep(tool.retry_backoff_ms / 1000 * (2 ** attempt))

        # All retries exhausted
        latency = (time.perf_counter() - t0) * 1000
        if self.audit:
            self.audit.log(
                "tool_call",
                tool_meta={
                    "tool_id": tool.id,
                    "tool_name": tool.name,
                    "input": input_data,
                    "error": str(last_error),
                    "success": False,
                    "attempts": tool.max_retries + 1,
                },
                latency_ms=latency,
            )
        raise ToolInvocationError(tool.name, f"Failed after {tool.max_retries + 1} attempts: {last_error}")

    async def _call(self, tool: ToolDefinition, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute a single tool call — dispatches to in-process mock, or HTTP."""
        schema_error = self._validate_input_schema(
            getattr(tool, "input_schema", None),
            input_data,
        )
        if schema_error:
            raise ToolInvocationError(tool.name, schema_error, recoverable=False)

        # 1. Try direct in-process call for mock tools (no HTTP needed)
        handler = self._get_mock_handler(tool)
        if handler is not None:
            return await handler(input_data)

        # 2. HTTP tools (api, webhook, rpc, or function with custom endpoint)
        return await self._call_http_tool(tool, input_data)

    def _get_mock_handler(self, tool: ToolDefinition):
        """Resolve a tool to a direct in-process handler if it matches a mock tool."""
        global _MOCK_TOOL_HANDLERS
        if not _MOCK_TOOL_HANDLERS:
            _MOCK_TOOL_HANDLERS = _lazy_init_handlers()

        # Function-category tools are selected by name in the console and do
        # not require an HTTP endpoint.
        if tool.category == "function":
            handler = _resolve_builtin_handler(tool.name)
            if handler:
                return handler

        # Check by endpoint URL path
        if tool.endpoint:
            handler = _resolve_mock_handler(tool.endpoint)
            if handler:
                return handler

        return None

    @classmethod
    def _validate_input_schema(
        cls,
        schema: dict[str, Any] | None,
        input_data: dict[str, Any],
    ) -> str | None:
        """Validate the common JSON-schema subset used by tool definitions."""
        if not schema:
            return None

        schema_type = schema.get("type")
        if schema_type and schema_type != "object":
            return None

        if not isinstance(input_data, dict):
            return "Tool input must be an object"

        properties = schema.get("properties") or {}
        required = schema.get("required") or []

        missing = [field for field in required if field not in input_data]
        if missing:
            return f"Missing required tool input field(s): {', '.join(missing)}"

        if schema.get("additionalProperties") is False:
            unknown = [field for field in input_data if field not in properties]
            if unknown:
                return f"Unknown tool input field(s): {', '.join(unknown)}"

        for field, value in input_data.items():
            field_schema = properties.get(field)
            if not isinstance(field_schema, dict):
                continue

            expected_type = field_schema.get("type")
            if expected_type and not cls._matches_json_type(value, expected_type):
                return f"Tool input field '{field}' must be {expected_type}"

            if "enum" in field_schema and value not in field_schema["enum"]:
                allowed = ", ".join(str(item) for item in field_schema["enum"])
                return f"Tool input field '{field}' must be one of: {allowed}"

            if isinstance(value, (int, float)) and not isinstance(value, bool):
                minimum = field_schema.get("minimum")
                maximum = field_schema.get("maximum")
                if minimum is not None and value < minimum:
                    return f"Tool input field '{field}' must be >= {minimum}"
                if maximum is not None and value > maximum:
                    return f"Tool input field '{field}' must be <= {maximum}"

            if isinstance(value, str):
                min_length = field_schema.get("minLength")
                max_length = field_schema.get("maxLength")
                if min_length is not None and len(value) < min_length:
                    return f"Tool input field '{field}' must be at least {min_length} characters"
                if max_length is not None and len(value) > max_length:
                    return f"Tool input field '{field}' must be at most {max_length} characters"

        return None

    @staticmethod
    def _matches_json_type(value: Any, expected_type: str | list[str]) -> bool:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        for item in types:
            if item == "string" and isinstance(value, str):
                return True
            if item == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
                return True
            if item == "integer" and isinstance(value, int) and not isinstance(value, bool):
                return True
            if item == "boolean" and isinstance(value, bool):
                return True
            if item == "object" and isinstance(value, dict):
                return True
            if item == "array" and isinstance(value, list):
                return True
            if item == "null" and value is None:
                return True
        return False

    @staticmethod
    def _is_localhost(url: str) -> bool:
        """Check if a URL points to the local machine."""
        host = urlparse(url).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1")

    async def _call_http_tool(self, tool: ToolDefinition, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute an HTTP tool call."""
        if not tool.endpoint:
            raise ToolInvocationError(tool.name, "No endpoint configured for HTTP tool", recoverable=False)

        # Circuit breaker check
        service_name = f"tool:{tool.endpoint}"
        if not circuit_breaker.can_execute(service_name):
            raise ToolInvocationError(tool.name, f"Circuit breaker open for {tool.endpoint}", recoverable=True)

        headers = {"Content-Type": "application/json"}

        # Apply auth
        if tool.auth_config:
            auth_type = tool.auth_config.get("type", "none")
            if auth_type == "bearer":
                headers["Authorization"] = f"Bearer {tool.auth_config.get('token', '')}"
            elif auth_type == "api_key":
                key_name = tool.auth_config.get("header", "X-API-Key")
                headers[key_name] = tool.auth_config.get("token", "")

        timeout = tool.timeout_ms / 1000
        # Bypass env proxy for loopback calls (avoids SOCKS5/HTTP proxy interfering)
        trust_env = not self._is_localhost(tool.endpoint)

        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=trust_env) as client:
                if tool.method.upper() == "GET":
                    resp = await client.get(tool.endpoint, params=input_data, headers=headers)
                elif tool.method.upper() == "POST":
                    resp = await client.post(tool.endpoint, json=input_data, headers=headers)
                elif tool.method.upper() == "PUT":
                    resp = await client.put(tool.endpoint, json=input_data, headers=headers)
                elif tool.method.upper() == "DELETE":
                    resp = await client.delete(tool.endpoint, params=input_data, headers=headers)
                else:
                    resp = await client.post(tool.endpoint, json=input_data, headers=headers)

                resp.raise_for_status()
                circuit_breaker.record_success(service_name)

                try:
                    return resp.json()
                except Exception:
                    return {"raw": resp.text}
        except ToolInvocationError:
            raise
        except Exception as e:
            circuit_breaker.record_failure(service_name)
            raise ToolInvocationError(tool.name, str(e), recoverable=True) from e

    async def test_connectivity(
        self,
        tool_id: str,
        test_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Test if a tool endpoint is reachable."""
        tool = await self.get_tool(tool_id)
        if tool is None:
            return {"success": False, "error": "Tool not found"}

        if test_input is not None:
            t0 = time.perf_counter()
            try:
                response = await self._call(tool, test_input)
                success = response.get("success", True) is not False
                return {
                    "success": success,
                    "status_code": 200 if success else 400,
                    "response": response,
                    "error": response.get("error") if not success else None,
                    "latency_ms": (time.perf_counter() - t0) * 1000,
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                    "latency_ms": (time.perf_counter() - t0) * 1000,
                }

        # Check for in-process mock handler first
        if self._get_mock_handler(tool):
            return {"success": True, "status_code": 200, "latency_ms": 0, "note": "In-process mock tool (no HTTP)"}

        if not tool.endpoint:
            return {"success": False, "error": "No endpoint configured"}

        t0 = time.perf_counter()
        try:
            trust_env = not self._is_localhost(tool.endpoint)
            async with httpx.AsyncClient(timeout=10, trust_env=trust_env) as client:
                resp = await client.request(tool.method.upper(), tool.endpoint, headers={"Content-Type": "application/json"})
                return {
                    "success": True,
                    "status_code": resp.status_code,
                    "latency_ms": (time.perf_counter() - t0) * 1000,
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "latency_ms": (time.perf_counter() - t0) * 1000,
            }
