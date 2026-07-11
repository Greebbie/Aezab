"""Unified LLM adapter — abstracts away provider differences.

Supports: OpenAI-compatible (Ollama, vLLM, etc.), DashScope (通义千问),
ZhipuAI (GLM), and any local model with an OpenAI-compatible endpoint.

Handles two reasoning model patterns:
1. Qwen3/DeepSeek-R1 via Ollama: empty content + separate `reasoning` field
2. MiniMax-M2.1/DeepSeek-R1 via vLLM: `<think>...</think>` tags in content
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from server.config import settings
from server.engine.circuit_breaker import circuit_breaker
from server.exceptions import LLMError, LLMModelError, LLMRateLimitError, LLMTimeoutError

logger = logging.getLogger(__name__)


class LLMStreamError(LLMError):
    """Raised when a token-streaming request fails mid-flight.

    Callers (agent_runtime's tool loop) catch this specifically and fall back
    to the existing non-streaming chat()/chat_with_tools() call for that
    round — streaming is strictly additive and never the only path.
    """

    def __init__(
        self,
        message: str = "LLM stream error",
        provider: str = "",
        model: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider, model, detail)


@dataclass
class LLMMessage:
    role: str  # system | user | assistant | tool
    content: str
    # For assistant messages with tool calls
    tool_calls: list[dict] | None = None
    # For tool result messages
    tool_call_id: str | None = None


@dataclass
class ToolCallRequest:
    """Parsed tool call from an LLM response."""
    id: str
    function_name: str
    arguments: dict[str, Any]
    raw: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    raw: dict = field(default_factory=dict)
    # Tool calls requested by the LLM (None if no tool calling)
    tool_calls: list[ToolCallRequest] | None = None
    # Reasoning/thinking content (Qwen3, DeepSeek-R1, etc.)
    reasoning: str | None = None


@dataclass
class StreamChunk:
    """A single incremental unit yielded by `LLMAdapter.chat_stream`."""
    delta: str = ""                      # incremental content text
    done: bool = False
    response: LLMResponse | None = None  # set on the final chunk (full accumulated response, incl. tool_calls)


def _merge_tool_call_deltas(acc: dict[int, dict], deltas: list[dict]) -> None:
    """Merge OpenAI-style streamed tool_call fragments into `acc`, keyed by index.

    Streaming tool_calls arrive as partial deltas indexed by position: the
    `id`/`name` typically arrive whole in the first fragment for that index,
    while `arguments` arrive incrementally and must be concatenated.
    """
    for d in deltas:
        idx = d.get("index", 0)
        entry = acc.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
        if d.get("id"):
            entry["id"] = d["id"]
        if d.get("type"):
            entry["type"] = d["type"]
        func_delta = d.get("function") or {}
        if func_delta.get("name"):
            entry["function"]["name"] += func_delta["name"]
        if func_delta.get("arguments"):
            entry["function"]["arguments"] += func_delta["arguments"]


# Pattern to match <think>...</think> blocks in content (vLLM-served models)
_THINK_TAG_PATTERN = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_think_tags(content: str) -> tuple[str, str | None]:
    """Strip <think>...</think> blocks from content.

    Many models served via vLLM (MiniMax-M2.1, DeepSeek-R1, etc.) embed their
    reasoning inside <think> XML tags within the content field itself.

    Returns:
        (clean_content, thinking_text) — thinking_text is None if no tags found
    """
    if not content or "<think>" not in content:
        return content, None

    # Extract all thinking blocks
    thinking_parts = re.findall(r"<think>(.*?)</think>", content, re.DOTALL)
    thinking_text = "\n".join(t.strip() for t in thinking_parts if t.strip()) or None

    # Remove <think>...</think> blocks from content
    clean = _THINK_TAG_PATTERN.sub("", content).strip()
    return clean, thinking_text


def _extract_content_from_reasoning(reasoning: str) -> str:
    """Extract the final answer from reasoning text when content is empty.

    Reasoning models sometimes put the entire response in the reasoning field.
    We try to find the "conclusion" or "final answer" portion.
    """
    if not reasoning:
        return ""

    # Look for common conclusion markers in reasoning
    for marker in ["最终回答：", "最终答案：", "回答：", "结论：", "总结：",
                    "所以，", "因此，", "综上，"]:
        idx = reasoning.rfind(marker)
        if idx >= 0:
            conclusion = reasoning[idx + len(marker):].strip()
            if len(conclusion) > 5:
                return conclusion

    # If reasoning is short enough (<200 chars), use it directly
    if len(reasoning) < 200:
        return reasoning.strip()

    # Take the last paragraph as the conclusion
    paragraphs = [p.strip() for p in reasoning.split("\n") if p.strip()]
    if paragraphs:
        last = paragraphs[-1]
        if len(last) > 5:
            return last

    return reasoning[:500].strip()


class LLMAdapter:
    """Unified async LLM client.

    Handles reasoning models (Qwen3, DeepSeek-R1) where the response may
    contain a `reasoning` field instead of or in addition to `content`.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.temperature = temperature if temperature is not None else settings.llm_temperature
        self.max_tokens = max_tokens or settings.llm_max_tokens
        self.timeout = timeout or getattr(settings, "llm_timeout", 60)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _make_client(self, timeout: int | None = None) -> httpx.AsyncClient:
        """Create an httpx client, bypassing proxy for local endpoints."""
        t = timeout or self.timeout
        # Bypass system proxy for local/private endpoints (Ollama, vLLM, etc.)
        is_local = any(h in self.base_url for h in ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"))
        return httpx.AsyncClient(timeout=t, trust_env=not is_local)

    def _to_llm_error(self, exc: Exception, model: str) -> LLMError:
        """Normalize provider/client failures into platform LLM errors."""
        if isinstance(exc, LLMError):
            return exc
        if isinstance(exc, httpx.TimeoutException):
            return LLMTimeoutError(
                "LLM request timed out",
                provider=self.base_url,
                model=model,
                detail={"timeout_seconds": self.timeout},
            )
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            body = exc.response.text[:1000]
            detail = {"status_code": status_code, "body": body}
            if status_code == 429:
                return LLMRateLimitError(
                    "LLM rate limit exceeded",
                    provider=self.base_url,
                    model=model,
                    detail=detail,
                )
            return LLMError(
                f"LLM HTTP {status_code}: {body[:200]}",
                provider=self.base_url,
                model=model,
                detail=detail,
            )
        if isinstance(exc, (KeyError, IndexError, TypeError, ValueError)):
            return LLMModelError(
                f"Invalid LLM response: {exc}",
                provider=self.base_url,
                model=model,
            )
        return LLMError(str(exc), provider=self.base_url, model=model)

    def _serialize_messages(self, messages: list[LLMMessage]) -> list[dict]:
        """Serialize LLMMessage list to OpenAI API format.

        Handles special message types: tool calls (assistant) and tool results.
        Converts ToolCallRequest dataclass objects to OpenAI-format dicts.
        """
        result = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls is not None:
                serialized = []
                for tc in m.tool_calls:
                    if isinstance(tc, dict):
                        serialized.append(tc)
                    elif hasattr(tc, "raw") and tc.raw:
                        serialized.append(tc.raw)
                    else:
                        serialized.append({
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function_name,
                                "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else str(tc.arguments),
                            },
                        })
                msg["tool_calls"] = serialized
            if m.tool_call_id is not None:
                msg["tool_call_id"] = m.tool_call_id
            result.append(msg)
        return result

    async def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """Non-streaming chat completion.

        Handles reasoning models where content may be empty and the
        actual answer lives in the `reasoning` field.
        """
        t0 = time.perf_counter()
        model = kwargs.get("model", self.model)
        payload = {
            "model": model,
            "messages": self._serialize_messages(messages),
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": False,
        }

        service_name = f"llm:{self.base_url}"
        if not circuit_breaker.can_execute(service_name):
            raise LLMError(
                f"Circuit breaker open for {self.base_url}",
                provider=self.base_url,
                model=model,
            )

        try:
            async with self._make_client() as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
            circuit_breaker.record_success(service_name)
        except Exception as exc:
            circuit_breaker.record_failure(service_name)
            raise self._to_llm_error(exc, model) from exc

        choice = data["choices"][0]
        message = choice["message"]
        usage = data.get("usage", {})

        content = message.get("content") or ""
        reasoning = message.get("reasoning") or ""

        # Pattern 1: <think> tags in content (vLLM-served MiniMax, DeepSeek-R1, etc.)
        content, think_text = _strip_think_tags(content)
        if think_text:
            logger.info(f"Stripped <think> tags from content ({len(think_text)} chars thinking)")
            reasoning = think_text if not reasoning else reasoning

        # Pattern 2: Empty content + separate reasoning field (Qwen3/Ollama)
        if not content.strip() and reasoning.strip():
            logger.info(f"Content empty, extracting from reasoning ({len(reasoning)} chars)")
            content = _extract_content_from_reasoning(reasoning)

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
            raw=data,
            reasoning=reasoning if reasoning else None,
        )

    async def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict],
        **kwargs,
    ) -> LLMResponse:
        """Chat completion with function calling / tool use.

        Args:
            messages: Conversation messages.
            tools: List of tool definitions in OpenAI format.

        Returns:
            LLMResponse with tool_calls populated if the LLM wants to call tools,
            or with content populated if the LLM gave a final answer.
        """
        t0 = time.perf_counter()
        model = kwargs.get("model", self.model)
        # Wrap tools in OpenAI format if needed: [{"type": "function", "function": {...}}]
        formatted_tools = []
        for t in tools:
            if "type" in t and "function" in t:
                formatted_tools.append(t)  # already in OpenAI format
            else:
                formatted_tools.append({"type": "function", "function": t})

        payload = {
            "model": model,
            "messages": self._serialize_messages(messages),
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "tools": formatted_tools,
            "stream": False,
        }

        service_name = f"llm:{self.base_url}"
        if not circuit_breaker.can_execute(service_name):
            raise LLMError(
                f"Circuit breaker open for {self.base_url}",
                provider=self.base_url,
                model=model,
            )

        try:
            async with self._make_client() as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
            circuit_breaker.record_success(service_name)
        except Exception as exc:
            circuit_breaker.record_failure(service_name)
            raise self._to_llm_error(exc, model) from exc

        choice = data["choices"][0]
        message = choice["message"]
        usage = data.get("usage", {})

        content = message.get("content") or ""
        reasoning = message.get("reasoning") or ""

        # Strip <think> tags from content (vLLM-served models)
        content, think_text = _strip_think_tags(content)
        if think_text:
            logger.info(f"Tool-call: stripped <think> tags ({len(think_text)} chars thinking)")
            reasoning = think_text if not reasoning else reasoning

        # Parse tool calls if present
        tool_calls = None
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            tool_calls = []
            for tc in raw_tool_calls:
                func = tc.get("function", {})
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append(ToolCallRequest(
                    id=tc.get("id", ""),
                    function_name=func.get("name", ""),
                    arguments=args,
                    raw=tc,
                ))

        # If no tool calls and content is empty, extract from reasoning
        if not tool_calls and not content.strip() and reasoning.strip():
            logger.info("Tool-call response: content empty, extracting from reasoning")
            content = _extract_content_from_reasoning(reasoning)

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
            raw=data,
            tool_calls=tool_calls,
            reasoning=reasoning if reasoning else None,
        )

    async def chat_stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """Token-streaming chat completion (OpenAI-compatible SSE wire format).

        Yields `StreamChunk(delta=...)` for each incremental content piece,
        followed by a final `StreamChunk(done=True, response=<full LLMResponse>)`
        once the stream ends. `tool_calls` fragments (indexed partial deltas)
        are merged by index; reasoning/reasoning_content deltas are buffered
        silently and only surfaced (via content extraction) if final content
        is empty — mirroring the non-streaming reasoning-model handling above.

        This method does NOT modify chat()/chat_with_tools() and is not used
        unless a caller explicitly opts into streaming. Any failure mid-flight
        raises `LLMStreamError`; callers should catch it and fall back to the
        non-streaming calls for that round.
        """
        t0 = time.perf_counter()
        model = kwargs.get("model", self.model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._serialize_messages(messages),
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": True,
            # Standard OpenAI-compatible opt-in for a final SSE chunk that
            # carries the `usage` block (without it, providers — MiniMax
            # included, verified empirically — omit usage entirely on
            # streamed responses, which is why audit traces showed 0 tokens
            # for the SSE path). Providers that don't recognize this field
            # are expected to ignore unknown JSON keys per the OpenAI-compatible
            # convention; if a specific provider ever hard-errors on it, add
            # a per-provider opt-out here rather than removing it globally.
            "stream_options": {"include_usage": True},
        }
        if tools:
            formatted_tools = []
            for t in tools:
                if "type" in t and "function" in t:
                    formatted_tools.append(t)  # already in OpenAI format
                else:
                    formatted_tools.append({"type": "function", "function": t})
            payload["tools"] = formatted_tools

        service_name = f"llm:{self.base_url}"
        if not circuit_breaker.can_execute(service_name):
            raise LLMStreamError(
                f"Circuit breaker open for {self.base_url}",
                provider=self.base_url,
                model=model,
            )

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_acc: dict[int, dict] = {}
        final_model = model
        usage: dict[str, Any] = {}

        try:
            async with self._make_client() as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                ) as resp:
                    resp.raise_for_status()
                    async for raw_line in resp.aiter_lines():
                        if not raw_line:
                            continue
                        line = raw_line.strip()
                        if not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        final_model = chunk.get("model") or final_model
                        if chunk.get("usage"):
                            usage = chunk["usage"]

                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}

                        piece = delta.get("content")
                        if piece:
                            content_parts.append(piece)
                            yield StreamChunk(delta=piece)

                        reasoning_piece = delta.get("reasoning") or delta.get("reasoning_content")
                        if reasoning_piece:
                            reasoning_parts.append(reasoning_piece)

                        tc_deltas = delta.get("tool_calls")
                        if tc_deltas:
                            _merge_tool_call_deltas(tool_call_acc, tc_deltas)
            circuit_breaker.record_success(service_name)
        except LLMStreamError:
            circuit_breaker.record_failure(service_name)
            raise
        except Exception as exc:
            circuit_breaker.record_failure(service_name)
            raise LLMStreamError(
                f"LLM stream error: {exc}",
                provider=self.base_url,
                model=model,
                detail={"original_error": str(exc)},
            ) from exc

        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)

        # Strip <think> tags that may have accumulated in streamed content
        # (mirrors chat()/chat_with_tools() handling for vLLM-served models).
        content, think_text = _strip_think_tags(content)
        if think_text:
            reasoning = think_text if not reasoning else reasoning

        tool_calls: list[ToolCallRequest] | None = None
        if tool_call_acc:
            tool_calls = []
            for idx in sorted(tool_call_acc.keys()):
                entry = tool_call_acc[idx]
                func = entry.get("function", {})
                arguments_str = func.get("arguments") or "{}"
                try:
                    args = json.loads(arguments_str)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append(ToolCallRequest(
                    id=entry.get("id", ""),
                    function_name=func.get("name", ""),
                    arguments=args,
                    raw={
                        "id": entry.get("id", ""),
                        "type": entry.get("type", "function"),
                        "function": {
                            "name": func.get("name", ""),
                            "arguments": arguments_str,
                        },
                    },
                ))

        # If no tool calls and content is empty, extract from reasoning
        if not tool_calls and not content.strip() and reasoning.strip():
            content = _extract_content_from_reasoning(reasoning)

        final_response = LLMResponse(
            content=content,
            model=final_model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
            raw={},
            tool_calls=tool_calls,
            reasoning=reasoning if reasoning else None,
        )
        yield StreamChunk(delta="", done=True, response=final_response)


# Thread-safe singleton default adapter
_default_adapter: LLMAdapter | None = None
_adapter_lock = threading.Lock()


def get_llm_adapter(**kwargs) -> LLMAdapter:
    global _default_adapter
    if kwargs:
        return LLMAdapter(**kwargs)
    if _default_adapter is None:
        with _adapter_lock:
            if _default_adapter is None:
                _default_adapter = LLMAdapter()
    return _default_adapter


def _adapter_from_config(config) -> LLMAdapter:
    """Create an LLMAdapter from a DB LLMConfig record.

    `config.api_key` is stored encrypted at rest (see
    `server.engine.secrets_store`) — decrypt it here so callers of this
    adapter always get the real credential. `decrypt_secret` transparently
    passes through legacy plaintext rows and returns "" on a corrupt/
    unrecoverable token rather than raising.
    """
    from server.engine.secrets_store import decrypt_secret

    return LLMAdapter(
        base_url=config.base_url,
        api_key=decrypt_secret(config.api_key),
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout_ms // 1000 if config.timeout_ms else None,
    )


async def get_llm_adapter_for_agent(agent, db) -> LLMAdapter:
    """Resolve the LLM adapter for an agent using a four-level chain:

    1. agent.llm_config_id → load full LLMConfig from DB
    2. agent.llm_model → override model name only (other params from env vars)
    3. Tenant default LLMConfig (is_default=True)
    4. Global env vars singleton
    """
    from sqlalchemy import select
    from server.models.llm_config import LLMConfig

    # 1. Explicit LLM config reference
    if agent.llm_config_id:
        result = await db.execute(
            select(LLMConfig).where(LLMConfig.id == agent.llm_config_id)
        )
        config = result.scalar_one_or_none()
        if config:
            logger.debug(f"Agent {agent.id}: using LLMConfig '{config.name}'")
            return _adapter_from_config(config)
        logger.warning(f"Agent {agent.id}: llm_config_id '{agent.llm_config_id}' not found, falling back")

    # 2. Model name override
    if agent.llm_model:
        logger.debug(f"Agent {agent.id}: using model override '{agent.llm_model}'")
        return get_llm_adapter(model=agent.llm_model)

    # 3. Tenant default config
    result = await db.execute(
        select(LLMConfig).where(
            LLMConfig.tenant_id == agent.tenant_id,
            LLMConfig.is_default.is_(True),
        )
    )
    default_config = result.scalar_one_or_none()
    if default_config:
        logger.debug(f"Agent {agent.id}: using tenant default LLMConfig '{default_config.name}'")
        return _adapter_from_config(default_config)

    # 4. Global env vars singleton
    return get_llm_adapter()
