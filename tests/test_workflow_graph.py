"""Executable routing contract used by the visual workflow editor."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from server.api.workflows import _validate_tool_ownership, _validate_workflow_steps
from server.schemas.workflow import StepCreate


def step(name, order, step_type="collect", **updates):
    return {"id": name.lower(), **StepCreate(
        name=name, order=order, step_type=step_type,
        fields=[{"name": "request", "label": "Request"}] if step_type == "collect" else None,
        **updates,
    ).model_dump()}


def rule(target, condition=None):
    return {"condition": condition, "goto_step": target}


def decision_config(**updates):
    return {
        "provider": "typesafe", "instructions": "Route the request",
        "input_fields": ["request"], "result_key": "route",
        "choices": [{"value": "refund", "description": "Refund requested"},
                    {"value": "other", "description": "Insufficient evidence"}],
        **updates,
    }


def test_both_rule_storage_formats_are_accepted():
    for rules in ([rule("Done")], {"rules": [rule("Done")]}):
        steps = [step("Collect", 0, next_step_rules=rules), step("Done", 1, "complete")]
        assert _validate_workflow_steps(steps) == []


def test_stable_id_routes_survive_target_rename():
    target = step("Renamed", 1, "complete")
    target["id"] = "persistent-target-id"
    assert _validate_workflow_steps([
        step("Collect", 0, next_step_rules={"rules": [rule(target["id"])]}), target,
    ]) == []


def test_default_must_be_last():
    errors = _validate_workflow_steps([
        step("Collect", 0, next_step_rules={"rules": [rule("Done"), rule("Done", {"field": "request", "op": "eq", "value": "x"})]}),
        step("Done", 1, "complete"),
    ])
    assert any("default route must be last" in error for error in errors)


@pytest.mark.parametrize("condition", [
    {"field": "request", "op": "regex", "value": "["},
    {"field": "request", "op": "gt", "value": "ten"},
])
def test_malformed_comparison_is_rejected(condition):
    assert _validate_workflow_steps([
        step("Collect", 0, next_step_rules={"rules": [rule("Done", condition)]}),
        step("Done", 1, "complete"),
    ])


def test_terminal_step_rejects_outgoing_route():
    errors = _validate_workflow_steps([
        step("Done", 0, "complete", next_step_rules={"rules": [rule("Collect")]}),
        step("Collect", 1),
    ])
    assert any("terminal" in error for error in errors)


def test_dangling_failure_route_is_rejected():
    errors = _validate_workflow_steps([step("Validate", 0, "validate", fallback_step_id="missing")])
    assert any("failure route points to unknown" in error for error in errors)


def test_automatic_loop_is_rejected():
    errors = _validate_workflow_steps([
        step("A", 0, "validate", next_step_rules={"rules": [rule("B")]}),
        step("B", 1, "validate", next_step_rules={"rules": [rule("A")]}),
    ])
    assert any("cycle" in error for error in errors)


def test_interactive_retry_loop_is_supported():
    assert _validate_workflow_steps([
        step("Collect", 0),
        step("Validate", 1, "validate", fallback_step_id="collect"),
        step("Done", 2, "complete"),
    ]) == []


def test_numeric_step_name_takes_precedence_over_order_in_cycle_detection():
    assert _validate_workflow_steps([
        step("1", 0),
        step("Validate", 1, "validate", next_step_rules={"rules": [rule("1")]}),
    ]) == []


def test_requester_confirmation_breaks_automatic_cycle():
    assert _validate_workflow_steps([
        step("Action", 0, "tool_call", tool_id="tool", requires_human_confirm=True),
        step("Validate", 1, "validate", next_step_rules={"rules": [rule("Action")]}),
    ]) == []


def test_no_implicit_edge_leaves_a_complete_step():
    assert _validate_workflow_steps([
        step("Done", 0, "complete"),
        step("A", 1, "validate", next_step_rules={"rules": [rule("Done")]}),
    ]) == []


@pytest.mark.parametrize("failure_type, config", [
    ("tool_call", {}),
    ("complete", {"webhook_enabled": True, "webhook_url": "https://example.org/hook"}),
])
def test_decision_cannot_fall_back_to_an_external_action(failure_type, config):
    errors = _validate_workflow_steps([
        step("Collect", 0),
        step("Route", 1, "decision", tool_config=decision_config(), fallback_step_id="fallback"),
        step("Fallback", 2, failure_type, tool_id="tool" if failure_type == "tool_call" else None, tool_config=config),
    ])
    assert any("failure must lead" in error for error in errors)


def test_decision_safe_pause_and_result_field():
    assert _validate_workflow_steps([
        step("Collect", 0),
        step("Route", 1, "decision", tool_config=decision_config(), fallback_step_id="pause"),
        step("Pause", 2, "human_review"),
    ]) == []


def test_decision_result_cannot_overwrite_collected_data():
    errors = _validate_workflow_steps([
        step("Collect", 0),
        step("Route", 1, "decision", tool_config=decision_config(result_key="request"), fallback_step_id="pause"),
        step("Pause", 2, "human_review"),
    ])
    assert errors


def test_decision_requires_known_input_and_other_outcome():
    errors = _validate_workflow_steps([
        step("Route", 0, "decision", tool_config=decision_config(input_fields=["unknown"]), fallback_step_id="pause"),
        step("Pause", 1, "human_review"),
    ])
    assert any("unknown input fields" in error for error in errors)


def test_decision_cannot_overwrite_mapped_tool_output():
    errors = _validate_workflow_steps([
        step("Collect", 0),
        step("Lookup", 1, "tool_call", tool_id="tool", tool_config={"output_mapping": {"ticket_id": "id"}}),
        step("Route", 2, "decision", tool_config=decision_config(result_key="ticket_id"), fallback_step_id="pause"),
        step("Pause", 3, "human_review"),
    ])
    assert any("result key must be unique" in error for error in errors)


def test_null_output_mapping_is_validation_error_not_server_error():
    errors = _validate_workflow_steps([
        step("Collect", 0),
        step("Lookup", 1, "tool_call", tool_id="tool", tool_config={"output_mapping": None}),
        step("Route", 2, "decision", tool_config=decision_config(), fallback_step_id="pause"),
        step("Pause", 3, "human_review"),
    ])
    assert any("output_mapping must be an object" in error for error in errors)


@pytest.mark.asyncio
async def test_cross_tenant_tool_is_rejected_without_leaking_ownership():
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    with pytest.raises(HTTPException, match="unavailable tool") as error:
        await _validate_tool_ownership(db, [step("Action", 0, "tool_call", tool_id="foreign")], "tenant-a")
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_tenant_owned_tool_is_allowed():
    result = MagicMock()
    result.scalars.return_value.all.return_value = ["owned"]
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    await _validate_tool_ownership(db, [step("Action", 0, "tool_call", tool_id="owned")], "tenant-a")
