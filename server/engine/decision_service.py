"""TypeSafe's typed Choice API. Policy and execution remain in Aezab."""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx

from server.config import settings
from server.engine.context_budget import estimate_tokens
from server.schemas.decision import DecisionConfig

SYSTEM_ONE_URL = "https://api.typesafe.ai/v1/systemone"
MAX_DECISION_INPUT_TOKENS = 8000


class DecisionUnavailable(Exception):
    """Safe machine-readable failure reason; never carries provider response bodies."""


@dataclass(frozen=True)
class DecisionResult:
    model: str
    choice: str
    confidence: float
    probabilities: dict[str, float]
    latency_ms: float

    def accepted(self, config: DecisionConfig) -> bool:
        return (
            self.choice != "other"
            and self.confidence >= config.min_confidence
            and self.probabilities[self.choice] >= config.min_probability
        )


def _probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionUnavailable("invalid_response")
    if not 0 <= value <= 1 or not math.isfinite(value):
        raise DecisionUnavailable("invalid_response")
    return float(value)


def parse_choice(payload: Any, config: DecisionConfig, latency_ms: float) -> DecisionResult:
    """Validate the complete distribution before any branch can be selected."""
    try:
        answer = payload["answers"]["route"]
        model = payload["model"]
        expected = {choice.value for choice in config.choices}
        if (
            not isinstance(model, str) or not model or len(model) > 128
            or answer["type"] != "choice" or answer["choice"] not in expected
            or set(answer["probabilities"]) != expected
        ):
            raise DecisionUnavailable("invalid_response")
        probabilities = {key: _probability(value) for key, value in answer["probabilities"].items()}
        if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.001):
            raise DecisionUnavailable("invalid_response")
        if probabilities[answer["choice"]] < max(probabilities.values()):
            raise DecisionUnavailable("invalid_response")
        return DecisionResult(
            model=model, choice=answer["choice"], confidence=_probability(answer["confidence"]),
            probabilities=probabilities, latency_ms=latency_ms,
        )
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise DecisionUnavailable("invalid_response") from exc


async def decide(config: DecisionConfig, collected: dict[str, Any]) -> DecisionResult:
    if not settings.typesafe_enabled or not settings.typesafe_api_key:
        raise DecisionUnavailable("not_configured")
    if any(field not in collected or collected[field] in (None, "", []) for field in config.input_fields):
        raise DecisionUnavailable("missing_input")
    payload = {
        "model": settings.typesafe_model,
        "state": {field: collected[field] for field in config.input_fields},
        "questions": {"route": {
            "type": "choice", "instructions": config.instructions,
            "criteria": {choice.value: choice.description for choice in config.choices},
        }},
    }
    # Do not silently truncate evidence and change the meaning of a decision.
    try:
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DecisionUnavailable("invalid_input") from exc
    if estimate_tokens(serialized) > MAX_DECISION_INPUT_TOKENS:
        raise DecisionUnavailable("input_budget_exceeded")
    start = time.perf_counter()
    try:
        # No redirects (credentials stay on the fixed provider host) or automatic
        # retries: a failed interactive decision takes its explicit fallback.
        async with httpx.AsyncClient(timeout=settings.typesafe_timeout, follow_redirects=False) as client:
            response = await client.post(
                SYSTEM_ONE_URL,
                headers={"Authorization": f"Bearer {settings.typesafe_api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except httpx.TimeoutException as exc:
        raise DecisionUnavailable("timeout") from exc
    except httpx.HTTPError as exc:
        raise DecisionUnavailable("provider_error") from exc
    except ValueError as exc:
        raise DecisionUnavailable("invalid_response") from exc
    return parse_choice(body, config, (time.perf_counter() - start) * 1000)
