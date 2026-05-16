"""Speech-to-text provider layer.

The ASR entry point is intentionally separate from AgentRuntime. Audio is
transcribed first, then the resulting text goes through the existing invoke
pipeline unchanged.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from server.config import settings


ASRProvider = Literal["dashscope_qwen", "funasr_http", "openai_compatible", "disabled"]

_SUPPORTED_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".flac",
    ".webm",
}

_CONFIG_LOCK = threading.Lock()
_CONFIG_FIELDS = {
    "provider",
    "base_url",
    "api_key",
    "model",
    "timeout",
    "max_file_mb",
    "funasr_path",
}


class ASRError(RuntimeError):
    """Base ASR error."""


class ASRValidationError(ASRError):
    """Audio input is invalid before provider invocation."""


@dataclass(slots=True)
class ASRConfig:
    provider: ASRProvider = "dashscope_qwen"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    model: str = "qwen3-asr-flash"
    timeout: int = 60
    max_file_mb: int = 20
    funasr_path: str = "/transcribe"


@dataclass(slots=True)
class ASRResult:
    text: str
    provider: str
    model: str
    language: str | None = None
    duration_seconds: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ASRService:
    """Unified ASR client for cloud APIs and self-hosted HTTP endpoints."""

    def __init__(self, config: ASRConfig | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config or config_from_settings()
        self._transport = transport

    async def transcribe(
        self,
        *,
        filename: str,
        content_type: str,
        audio_bytes: bytes,
        language: str | None = None,
        prompt: str | None = None,
    ) -> ASRResult:
        self._validate_audio(filename, audio_bytes)

        if self.config.provider == "disabled":
            raise ASRValidationError("ASR is disabled")
        if self.config.provider in ("dashscope_qwen", "openai_compatible"):
            return await self._transcribe_openai_audio(
                filename=filename,
                content_type=content_type,
                audio_bytes=audio_bytes,
                language=language,
                prompt=prompt,
            )
        if self.config.provider == "funasr_http":
            return await self._transcribe_http_multipart(
                filename=filename,
                content_type=content_type,
                audio_bytes=audio_bytes,
                language=language,
                prompt=prompt,
            )
        raise ASRValidationError(f"Unsupported ASR provider: {self.config.provider}")

    def _validate_audio(self, filename: str, audio_bytes: bytes) -> None:
        if not audio_bytes:
            raise ASRValidationError("Audio file is empty")

        max_file_mb = effective_max_file_mb(self.config)
        max_bytes = max_file_mb * 1024 * 1024
        if len(audio_bytes) > max_bytes:
            raise ASRValidationError(
                f"Audio file exceeds {max_file_mb}MB limit"
            )

        ext = os.path.splitext(filename.lower())[1]
        if ext and ext not in _SUPPORTED_EXTENSIONS:
            raise ASRValidationError(
                f"Unsupported audio file type '{ext}'. Allowed: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
            )

    async def _transcribe_openai_audio(
        self,
        *,
        filename: str,
        content_type: str,
        audio_bytes: bytes,
        language: str | None,
        prompt: str | None,
    ) -> ASRResult:
        url = self._join_url(self.config.base_url, "/chat/completions")
        encoded = base64.b64encode(audio_bytes).decode("ascii")
        data_uri = f"data:{content_type or 'application/octet-stream'};base64,{encoded}"
        content: list[dict[str, Any]] = [
            {"type": "input_audio", "input_audio": {"data": data_uri}},
        ]
        if self.config.provider != "dashscope_qwen" and prompt:
            content.append({"type": "text", "text": prompt})
        payload: dict[str, Any] = {
            "model": self.config.model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
        }
        if self.config.provider == "dashscope_qwen" or language:
            payload["asr_options"] = {"enable_itn": False}
            if language:
                payload["asr_options"]["language"] = language

        raw = await self._post_json(url, payload)
        text = _extract_text(raw)
        if not text:
            raise ASRError("ASR provider returned an empty transcript")
        return ASRResult(
            text=text,
            provider=self.config.provider,
            model=self.config.model,
            language=language or _extract_language(raw),
            duration_seconds=_extract_duration(raw),
            raw=raw,
        )

    async def _transcribe_http_multipart(
        self,
        *,
        filename: str,
        content_type: str,
        audio_bytes: bytes,
        language: str | None,
        prompt: str | None,
    ) -> ASRResult:
        url = self._resolve_funasr_url()
        data = {"model": self.config.model}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        raw = await self._post_multipart(
            url,
            files={"file": (filename, audio_bytes, content_type or "application/octet-stream")},
            data=data,
        )
        text = _extract_text(raw)
        if not text:
            raise ASRError("ASR provider returned an empty transcript")
        return ASRResult(
            text=text,
            provider=self.config.provider,
            model=self.config.model,
            language=language or _extract_language(raw),
            duration_seconds=_extract_duration(raw),
            raw=raw,
        )

    async def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        async with self._client() as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def _post_multipart(
        self,
        url: str,
        *,
        files: dict[str, tuple[str, bytes, str]],
        data: dict[str, str],
    ) -> dict[str, Any]:
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        async with self._client() as client:
            resp = await client.post(url, data=data, files=files, headers=headers)
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                return {"text": resp.text}

    def _client(self) -> httpx.AsyncClient:
        is_local = any(
            host in self.config.base_url
            for host in ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")
        )
        return httpx.AsyncClient(
            timeout=self.config.timeout,
            transport=self._transport,
            trust_env=not is_local,
        )

    def _resolve_funasr_url(self) -> str:
        parsed = urlparse(self.config.base_url)
        if parsed.path and parsed.path != "/":
            return self.config.base_url.rstrip("/")
        return self._join_url(self.config.base_url, self.config.funasr_path)

    @staticmethod
    def _join_url(base: str, path: str) -> str:
        return base.rstrip("/") + "/" + path.lstrip("/")


def config_from_settings() -> ASRConfig:
    overrides = _load_asr_config_overrides()
    provider = overrides.get("provider", settings.asr_provider)
    base_url = overrides.get("base_url", settings.asr_base_url)
    return ASRConfig(
        provider=provider,
        base_url=base_url,
        api_key=_resolve_api_key(base_url=base_url, overrides=overrides),
        model=overrides.get("model", settings.asr_model),
        timeout=int(overrides.get("timeout", settings.asr_timeout)),
        max_file_mb=int(overrides.get("max_file_mb", settings.asr_max_file_mb)),
        funasr_path=overrides.get("funasr_path", settings.asr_funasr_path),
    )


def save_asr_config_update(update: dict[str, Any]) -> ASRConfig:
    overrides = _load_asr_config_overrides()

    if update.get("clear_api_key"):
        overrides.pop("api_key", None)

    for key in _CONFIG_FIELDS:
        if key not in update:
            continue
        value = update[key]
        if value is None:
            continue
        if key == "api_key":
            if str(value).strip():
                overrides["api_key"] = str(value).strip()
            continue
        overrides[key] = value

    _write_asr_config_overrides(overrides)
    return config_from_settings()


def effective_max_file_mb(config: ASRConfig) -> int:
    if config.provider == "dashscope_qwen":
        return min(config.max_file_mb, 10)
    return config.max_file_mb


def asr_uses_llm_api_key(config: ASRConfig) -> bool:
    overrides = _load_asr_config_overrides()
    if "api_key" in overrides or settings.asr_api_key:
        return False
    return bool(config.api_key) and config.base_url.rstrip("/") == settings.llm_base_url.rstrip("/")


def get_asr_config_metadata() -> dict[str, Any]:
    overrides = _load_asr_config_overrides()
    path = Path(settings.asr_config_path)
    has_saved_api_key = bool(str(overrides.get("api_key") or "").strip())

    if has_saved_api_key:
        api_key_source = "saved_config"
    elif settings.asr_api_key:
        api_key_source = "environment"
    elif settings.asr_base_url.rstrip("/") == settings.llm_base_url.rstrip("/") and settings.llm_api_key:
        api_key_source = "llm_config"
    else:
        api_key_source = "missing"

    return {
        "config_path": str(path),
        "config_file_exists": path.exists(),
        "has_saved_config": bool(overrides),
        "has_saved_api_key": has_saved_api_key,
        "api_key_source": api_key_source,
    }


def _resolve_api_key(*, base_url: str, overrides: dict[str, Any]) -> str:
    """Reuse the LLM key only when ASR and LLM point to the same provider."""
    if "api_key" in overrides:
        return str(overrides["api_key"] or "").strip()
    if settings.asr_api_key:
        return settings.asr_api_key
    if base_url.rstrip("/") == settings.llm_base_url.rstrip("/"):
        return settings.llm_api_key
    return ""


def _load_asr_config_overrides() -> dict[str, Any]:
    path = Path(settings.asr_config_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if key in _CONFIG_FIELDS}


def _write_asr_config_overrides(data: dict[str, Any]) -> None:
    path = Path(settings.asr_config_path)
    with _CONFIG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        clean = {key: value for key, value in data.items() if key in _CONFIG_FIELDS}
        path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if not isinstance(raw, dict):
        return ""

    for key in ("text", "transcript"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    result = raw.get("result")
    if isinstance(result, dict):
        text = _extract_text(result)
        if text:
            return text
    elif isinstance(result, str) and result.strip():
        return result.strip()

    data = raw.get("data")
    if isinstance(data, dict):
        text = _extract_text(data)
        if text:
            return text

    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text") or item.get("transcript")
                        if isinstance(text, str) and text.strip():
                            return text.strip()
    return ""


def _extract_language(raw: dict[str, Any]) -> str | None:
    annotations = (
        raw.get("choices", [{}])[0]
        .get("message", {})
        .get("annotations", [])
        if isinstance(raw.get("choices"), list) and raw.get("choices")
        else []
    )
    if isinstance(annotations, list):
        for item in annotations:
            if isinstance(item, dict) and isinstance(item.get("language"), str):
                return item["language"]
    for key in ("language", "lang"):
        value = raw.get(key)
        if isinstance(value, str):
            return value
    return None


def _extract_duration(raw: dict[str, Any]) -> float | None:
    usage = raw.get("usage")
    if isinstance(usage, dict):
        seconds = usage.get("seconds") or usage.get("duration")
        if isinstance(seconds, (int, float)):
            return float(seconds)
    for key in ("duration", "duration_seconds"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None
