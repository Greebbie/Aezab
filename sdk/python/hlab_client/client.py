"""HlAB official Python SDK — sync + async clients for the Headless AI Agent
Platform API. Zero dependencies beyond httpx. Mirrors the surface of the
JS/TS SDK (sdk/js/src/index.ts).

Endpoint contract (see server/api/invoke.py, sessions.py, files.py):
  POST   {prefix}/invoke                 -> InvokeResponse JSON
  POST   {prefix}/invoke/stream          -> SSE (answer_delta/status/answer/done/error)
  GET    {prefix}/sessions/               -> {total, offset, limit, items: [...]}
  GET    {prefix}/sessions/{id}/messages  -> {total, offset, limit, items: [...]}
  DELETE {prefix}/sessions/{id}           -> 204
  POST   {prefix}/files/upload            -> {file_id, filename, size, content_type, reference}

Auth: X-API-Key header (an API key scoped for "invoke", see docs/integration.md).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Iterator

import httpx

__all__ = ["HlabClient", "AsyncHlabClient", "HlabAPIError", "parse_sse_lines"]

_DEFAULT_TIMEOUT = 120.0
_DEFAULT_API_PREFIX = "/api/v1"


class HlabAPIError(Exception):
    """Raised when the HlAB API returns a non-2xx response.

    `detail` is whatever the JSON body's "detail" field contained (FastAPI's
    default error envelope), or the raw response text if the body wasn't JSON.
    """

    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HlAB API error {status_code}: {detail}")


# ---------------------------------------------------------------------------
# SSE parsing — shared between the sync and async clients so the framing
# logic (blank line terminates an event, "event:"/"data:" fields, "data:"
# lines join with "\n", ":"-prefixed lines are comments) is written once.
# ---------------------------------------------------------------------------


class _SSEAccumulator:
    """Incremental SSE event parser. Feed one raw line at a time."""

    def __init__(self) -> None:
        self._event_type = "message"
        self._data_lines: list[str] = []

    def feed(self, raw_line: str) -> dict[str, Any] | None:
        line = raw_line.rstrip("\r\n")
        if line == "":
            return self._flush()
        if line.startswith(":"):
            return None  # comment line
        if line.startswith("event:"):
            self._event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            self._data_lines.append(line[len("data:"):].lstrip(" "))
        return None

    def flush_final(self) -> dict[str, Any] | None:
        """Flush a trailing event that never got a final blank-line terminator."""
        return self._flush()

    def _flush(self) -> dict[str, Any] | None:
        if not self._data_lines:
            self._event_type = "message"
            return None
        raw = "\n".join(self._data_lines)
        try:
            data: Any = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            data = raw
        event = {"event": self._event_type, "data": data}
        self._event_type = "message"
        self._data_lines = []
        return event


def parse_sse_lines(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Parse an iterable of raw SSE text lines into `{"event": str, "data": Any}` dicts.

    `data` is JSON-decoded when possible, otherwise the raw joined string.
    Usable directly against a canned list of lines (unit tests) or against
    `httpx.Response.iter_lines()`.
    """
    acc = _SSEAccumulator()
    for raw_line in lines:
        event = acc.feed(raw_line)
        if event is not None:
            yield event
    event = acc.flush_final()
    if event is not None:
        yield event


async def _parse_sse_lines_async(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Async counterpart of `parse_sse_lines`, for `httpx.Response.aiter_lines()`."""
    acc = _SSEAccumulator()
    async for raw_line in lines:
        event = acc.feed(raw_line)
        if event is not None:
            yield event
    event = acc.flush_final()
    if event is not None:
        yield event


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _invoke_payload(
    agent_id: str,
    message: str,
    session_id: str | None,
    user_id: str | None,
    form_data: dict[str, Any] | None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"agent_id": agent_id, "message": message}
    if session_id is not None:
        payload["session_id"] = session_id
    if user_id is not None:
        payload["user_id"] = user_id
    if form_data is not None:
        payload["form_data"] = form_data
    payload.update(extra)
    return payload


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        body = response.json()
        detail = body.get("detail", response.text) if isinstance(body, dict) else body
    except Exception:
        detail = response.text
    raise HlabAPIError(response.status_code, detail)


def _base_url_with_prefix(base_url: str, api_prefix: str) -> str:
    return base_url.rstrip("/") + api_prefix


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------


class HlabClient:
    """Synchronous client for the HlAB Headless Agent API.

    Example:
        with HlabClient("https://your-hlab-host", api_key="...") as client:
            result = client.invoke("sales_agent", "Hello")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        api_prefix: str = _DEFAULT_API_PREFIX,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        kwargs: dict[str, Any] = {
            "base_url": _base_url_with_prefix(base_url, api_prefix),
            "headers": headers,
            "timeout": timeout,
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HlabClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ---- invoke ----

    def invoke(
        self,
        agent_id: str,
        message: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        form_data: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Send a message to an agent and get the full response (POST /invoke)."""
        payload = _invoke_payload(agent_id, message, session_id, user_id, form_data, **extra)
        response = self._client.post("/invoke", json=payload)
        _raise_for_status(response)
        return response.json()

    def invoke_stream(
        self,
        agent_id: str,
        message: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        form_data: dict[str, Any] | None = None,
        **extra: Any,
    ) -> Iterator[dict[str, Any]]:
        """Stream a message to an agent (POST /invoke/stream).

        Yields `{"event": ..., "data": {...}}` dicts as SSE events arrive
        (answer_delta / status / answer / done / error — see docs/integration.md).
        """
        payload = _invoke_payload(agent_id, message, session_id, user_id, form_data, **extra)
        with self._client.stream("POST", "/invoke/stream", json=payload) as response:
            if not response.is_success:
                response.read()
                _raise_for_status(response)
            yield from parse_sse_lines(response.iter_lines())

    # ---- sessions ----

    def list_sessions(
        self,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if agent_id is not None:
            params["agent_id"] = agent_id
        if user_id is not None:
            params["user_id"] = user_id
        response = self._client.get("/sessions/", params=params)
        _raise_for_status(response)
        return response.json()

    def get_messages(
        self, session_id: str, *, limit: int = 100, offset: int = 0,
    ) -> dict[str, Any]:
        response = self._client.get(
            f"/sessions/{session_id}/messages", params={"limit": limit, "offset": offset},
        )
        _raise_for_status(response)
        return response.json()

    def delete_session(self, session_id: str) -> None:
        response = self._client.delete(f"/sessions/{session_id}")
        _raise_for_status(response)

    # ---- files ----

    def upload_file(self, path: str | Path) -> dict[str, Any]:
        file_path = Path(path)
        with file_path.open("rb") as fh:
            response = self._client.post(
                "/files/upload", files={"file": (file_path.name, fh)},
            )
        _raise_for_status(response)
        return response.json()


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


class AsyncHlabClient:
    """Async counterpart of `HlabClient`, built on `httpx.AsyncClient`.

    Example:
        async with AsyncHlabClient("https://your-hlab-host", api_key="...") as client:
            async for event in client.invoke_stream("sales_agent", "Hello"):
                print(event["event"], event["data"])
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        api_prefix: str = _DEFAULT_API_PREFIX,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        kwargs: dict[str, Any] = {
            "base_url": _base_url_with_prefix(base_url, api_prefix),
            "headers": headers,
            "timeout": timeout,
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncHlabClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    # ---- invoke ----

    async def invoke(
        self,
        agent_id: str,
        message: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        form_data: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Send a message to an agent and get the full response (POST /invoke)."""
        payload = _invoke_payload(agent_id, message, session_id, user_id, form_data, **extra)
        response = await self._client.post("/invoke", json=payload)
        _raise_for_status(response)
        return response.json()

    async def invoke_stream(
        self,
        agent_id: str,
        message: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        form_data: dict[str, Any] | None = None,
        **extra: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a message to an agent (POST /invoke/stream).

        Yields `{"event": ..., "data": {...}}` dicts as SSE events arrive.
        """
        payload = _invoke_payload(agent_id, message, session_id, user_id, form_data, **extra)
        async with self._client.stream("POST", "/invoke/stream", json=payload) as response:
            if not response.is_success:
                await response.aread()
                _raise_for_status(response)
            async for event in _parse_sse_lines_async(response.aiter_lines()):
                yield event

    # ---- sessions ----

    async def list_sessions(
        self,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if agent_id is not None:
            params["agent_id"] = agent_id
        if user_id is not None:
            params["user_id"] = user_id
        response = await self._client.get("/sessions/", params=params)
        _raise_for_status(response)
        return response.json()

    async def get_messages(
        self, session_id: str, *, limit: int = 100, offset: int = 0,
    ) -> dict[str, Any]:
        response = await self._client.get(
            f"/sessions/{session_id}/messages", params={"limit": limit, "offset": offset},
        )
        _raise_for_status(response)
        return response.json()

    async def delete_session(self, session_id: str) -> None:
        response = await self._client.delete(f"/sessions/{session_id}")
        _raise_for_status(response)

    # ---- files ----

    async def upload_file(self, path: str | Path) -> dict[str, Any]:
        file_path = Path(path)
        content = file_path.read_bytes()
        response = await self._client.post(
            "/files/upload", files={"file": (file_path.name, content)},
        )
        _raise_for_status(response)
        return response.json()
