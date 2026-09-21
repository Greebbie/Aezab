"""Offline contract tests: no TypeSafe credentials or paid requests required."""
import json

import httpx
import pytest
from pydantic import ValidationError

from server.engine import decision_service as service
from server.schemas.decision import DecisionConfig


@pytest.fixture
def config():
    return DecisionConfig(
        instructions="Which team handles this request?",
        input_fields=["request"],
        choices=[
            {"value": "support", "description": "Product support"},
            {"value": "other", "description": "No match or insufficient evidence"},
        ],
    )


def response(**overrides):
    answer = {
        "type": "choice", "choice": "support", "confidence": 0.92,
        "probabilities": {"support": 0.95, "other": 0.05},
        **overrides,
    }
    return {"model": "jev-1.13.0", "answers": {"route": answer}}


def test_choice_requires_both_thresholds_and_a_match(config):
    assert service.parse_choice(response(), config, 10).accepted(config)
    for answer in (
        response(confidence=0.2),
        response(probabilities={"support": 0.6, "other": 0.4}),
        response(choice="other", probabilities={"support": 0.05, "other": 0.95}),
    ):
        assert not service.parse_choice(answer, config, 10).accepted(config)


@pytest.mark.parametrize("answer", [
    None, [], {}, {"model": "jev", "answers": {"route": []}},
    response(type="score"), response(choice="invented"),
    response(probabilities={"support": 1}),
    response(probabilities={"support": 0.8, "other": 0.8}),
    response(probabilities={"support": 0.1, "other": 0.9}),
    response(probabilities={"support": True, "other": 0}),
    response(confidence=True), response(confidence="0.9"),
    response(confidence=float("nan")), response(confidence=float("inf")),
    response(confidence=10**400),
    response(probabilities={"support": 10**400, "other": 0}),
])
def test_invalid_response_is_a_safe_failure(config, answer):
    with pytest.raises(service.DecisionUnavailable, match="invalid_response"):
        service.parse_choice(answer, config, 1)


@pytest.mark.parametrize("overrides", [
    {"choices": [{"value": "support", "description": "Support"}]},
    {"choices": [{"value": "support", "description": "A"}, {"value": "support", "description": "B"}]},
    {"input_fields": ["_private"]}, {"input_fields": []},
    {"input_fields": ["request", "request"]}, {"result_key": "request"},
    {"min_confidence": float("nan")}, {"min_probability": 2},
    {"provider": "arbitrary"}, {"api_key": "never-store-credentials-here"},
])
def test_rejects_invalid_policy(config, overrides):
    with pytest.raises(ValidationError):
        DecisionConfig.model_validate({**config.model_dump(), **overrides})


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(service.settings, "typesafe_enabled", True)
    monkeypatch.setattr(service.settings, "typesafe_api_key", "test-only-key")
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


@pytest.mark.asyncio
async def test_request_sends_only_declared_state_and_fixed_provider(config, monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == service.SYSTEM_ONE_URL
        assert request.headers["Authorization"] == "Bearer test-only-key"
        body = json.loads(request.content)
        assert body["state"] == {"request": "Printer broken"}
        assert body["questions"]["route"]["criteria"] == {"support": "Product support", "other": "No match or insufficient evidence"}
        return httpx.Response(200, json=response())

    install_transport(monkeypatch, handle)
    result = await service.decide(config, {"request": "Printer broken", "password": "private", "_tool_result": "private"})
    assert result.accepted(config)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 401, 429, 500])
async def test_http_errors_are_not_retried_or_redirected(config, monkeypatch, status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive provider error", headers={"Location": "https://example.com"})

    install_transport(monkeypatch, handle)
    with pytest.raises(service.DecisionUnavailable, match="^provider_error$"):
        await service.decide(config, {"request": "broken"})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_timeout_is_a_safe_failure(config, monkeypatch):
    def handle(request):
        raise httpx.ReadTimeout("sensitive details", request=request)

    install_transport(monkeypatch, handle)
    with pytest.raises(service.DecisionUnavailable, match="^timeout$"):
        await service.decide(config, {"request": "broken"})


@pytest.mark.asyncio
@pytest.mark.parametrize("state, reason", [({}, "missing_input"), ({"request": ""}, "missing_input"), ({"request": "长" * 9000}, "input_budget_exceeded")])
async def test_rejects_missing_or_oversized_state_before_network(config, monkeypatch, state, reason):
    def handle(request):
        pytest.fail("Must not call the provider")

    install_transport(monkeypatch, handle)
    with pytest.raises(service.DecisionUnavailable, match=reason):
        await service.decide(config, state)


@pytest.mark.asyncio
async def test_disabled_provider_never_calls_network(config, monkeypatch):
    monkeypatch.setattr(service.settings, "typesafe_enabled", False)
    with pytest.raises(service.DecisionUnavailable, match="not_configured"):
        await service.decide(config, {"request": "broken"})
