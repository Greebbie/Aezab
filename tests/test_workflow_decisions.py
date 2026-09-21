"""Execute saved branch connections and safe semantic decision failures."""
from unittest.mock import AsyncMock, Mock
import json
from pathlib import Path

import pytest

from server.engine import workflow_executor as module
from server.engine.decision_service import DecisionResult, DecisionUnavailable
from server.engine.workflow_executor import WorkflowExecutor, validate_field
from server.models.session import ConversationSession
from server.models.workflow import WorkflowStep


def fixture():
    config = {
        "provider": "typesafe", "instructions": "Choose a department", "input_fields": ["request"],
        "choices": [{"value": "support", "description": "Product support"}, {"value": "other", "description": "No match"}],
        "result_key": "route", "min_confidence": 0.8, "min_probability": 0.8,
    }
    decision = WorkflowStep(
        id="decide", workflow_id="wf", name="Route", order=0, step_type="decision",
        tool_config=config, fallback_step_id="clarify",
        next_step_rules={"rules": [{"condition": {"field": "route", "op": "eq", "value": "support"}, "goto_step": "confirm"}]},
    )
    confirm = WorkflowStep(id="confirm", workflow_id="wf", name="Confirm", order=1, step_type="confirm")
    clarify = WorkflowStep(id="clarify", workflow_id="wf", name="Clarify", order=2, step_type="collect", fields=[{"name": "request", "label": "Request", "required": True}])
    steps = [decision, confirm, clarify]
    session = ConversationSession(
        id="s", agent_id="a", tenant_id="t", collected_data={"request": "Printer broken", "route": "stale"},
        workflow_state={"workflow_id": "wf", "current_step_index": 0, "status": "in_progress"},
    )
    audit = Mock()
    executor = WorkflowExecutor(db=None, tool_gateway=None, audit=audit)
    executor.get_steps = AsyncMock(return_value=steps)
    return executor, steps, session, audit


@pytest.mark.asyncio
async def test_accepted_decision_follows_saved_id_branch(monkeypatch):
    executor, steps, session, audit = fixture()
    monkeypatch.setattr(module, "decide", AsyncMock(return_value=DecisionResult("jev-1.13.0", "support", 0.9, {"support": 0.95, "other": 0.05}, 12)))
    result = await executor.process_step(session, "")
    assert result.status == "waiting_input"
    assert result.card.step_type == "confirm"
    assert session.collected_data["route"] == "support"
    assert session.workflow_state["current_step_index"] == 1
    assert session.workflow_state["decisions"]["decide"]["accepted"] is True
    event = next(call for call in audit.log.call_args_list if call.args[0] == "workflow_decision")
    assert "Printer broken" not in str(event)
    assert event.kwargs["workflow_meta"]["model"] == "jev-1.13.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("judgment", [
    DecisionUnavailable("timeout"), DecisionUnavailable("not_configured"),
    DecisionUnavailable("invalid_response"), DecisionUnavailable("input_budget_exceeded"),
    DecisionResult("jev", "support", 0.4, {"support": 0.9, "other": 0.1}, 1),
    DecisionResult("jev", "support", 0.9, {"support": 0.6, "other": 0.4}, 1),
    DecisionResult("jev", "other", 0.95, {"support": 0.01, "other": 0.99}, 1),
])
async def test_failed_judgment_takes_fallback_without_stale_choice(monkeypatch, judgment):
    executor, steps, session, _ = fixture()
    mock = AsyncMock(side_effect=judgment) if isinstance(judgment, Exception) else AsyncMock(return_value=judgment)
    monkeypatch.setattr(module, "decide", mock)
    result = await executor.process_step(session, "")
    assert result.status == "waiting_input"
    assert result.card.step_name == "Clarify"
    assert session.workflow_state["current_step_index"] == 2
    assert "route" not in session.collected_data


@pytest.mark.asyncio
async def test_failed_judgment_cannot_fall_back_to_tool(monkeypatch):
    executor, steps, session, _ = fixture()
    steps[2].step_type = "tool_call"
    monkeypatch.setattr(module, "decide", AsyncMock(side_effect=DecisionUnavailable("timeout")))
    result = await executor.process_step(session, "")
    assert result.status == "error"
    assert session.workflow_state["current_step_index"] == 0


@pytest.mark.asyncio
async def test_missing_branch_target_stops_instead_of_linear_fallthrough():
    executor, steps, session, _ = fixture()
    steps[0].next_step_rules = {"rules": [{"condition": None, "goto_step": "deleted-id"}]}
    result = await executor._advance(steps, 0, session)
    assert result.status == "error"
    assert session.workflow_state["current_step_index"] == 0


@pytest.mark.asyncio
async def test_resume_clears_pause_and_returns_interactive_card():
    executor, steps, session, _ = fixture()
    steps[0].step_type = "human_review"
    steps[0].next_step_rules = None
    session.workflow_state = {**session.workflow_state, "status": "paused_for_review"}
    result = await executor.resume_after_review(session)
    assert result.status == "waiting_input"
    assert session.workflow_state["status"] == "in_progress"


def test_field_options_and_zero_are_validated():
    select = {"field_type": "select", "label": "Department", "options": [{"value": "support"}]}
    assert validate_field("invented", select)
    assert validate_field("support", select) is None
    multi = {**select, "field_type": "multi_select"}
    assert validate_field(["support"], multi) is None
    assert validate_field(["support", "invented"], multi)
    assert validate_field(0, {"field_type": "number", "required": True}) is None


def test_bad_legacy_regex_fails_validation_without_crashing():
    from server.engine.rule_evaluator import evaluate_rules

    assert validate_field("x", {"validation_rule": "["})
    assert evaluate_rules([{"condition": {"field": "request", "op": "regex", "value": "["}, "goto_step": "unsafe"}], {"request": "x"}) is None


@pytest.mark.asyncio
async def test_zero_survives_incremental_field_collection():
    executor, steps, session, _ = fixture()
    step = steps[0]
    step.step_type = "collect"
    step.next_step_rules = None
    step.fields = [
        {"name": "amount", "label": "Amount", "field_type": "number", "required": True},
        {"name": "reason", "label": "Reason", "required": True},
    ]
    session.collected_data = {}
    first = await executor.process_step(session, "", {"amount": 0})
    assert first.status == "waiting_input"
    second = await executor.process_step(session, "", {"reason": "No charge"})
    assert second.card.step_type == "confirm"
    assert session.collected_data["amount"] == 0


@pytest.mark.asyncio
async def test_tool_confirmation_rejects_ambiguous_text_and_executes_once():
    executor, steps, session, _ = fixture()
    steps[0].step_type = "tool_call"
    steps[0].requires_human_confirm = True
    steps[0].tool_id = "write-tool"
    steps[0].tool_config = {}
    steps[0].next_step_rules = None
    executor.tool_gw = Mock(invoke=AsyncMock(return_value={"ticket": "demo-1"}))
    first = await executor.process_step(session, "confirm")
    assert first.card.step_type == "confirm"
    executor.tool_gw.invoke.assert_not_called()
    for message in ["Is this correct?", "confirmation pending"]:
        result = await executor.process_step(session, message)
        assert result.card.step_type == "confirm"
        executor.tool_gw.invoke.assert_not_called()
    await executor.process_step(session, "确认")
    executor.tool_gw.invoke.assert_awaited_once()
    assert "pending_confirmation_step" not in session.workflow_state


@pytest.mark.asyncio
async def test_rejecting_tool_confirmation_returns_to_editable_collect():
    executor, steps, session, _ = fixture()
    collect = steps[0]
    collect.step_type = "collect"
    collect.fields = [{"name": "request", "label": "Request", "required": True}]
    collect.next_step_rules = None
    steps[1].step_type = "tool_call"
    steps[1].requires_human_confirm = True
    steps[1].tool_id = "write"
    executor.tool_gw = Mock(invoke=AsyncMock())
    await executor.process_step(session, "", {"request": "Change me"})
    result = await executor.process_step(session, "no")
    assert result.status == "waiting_input"
    assert result.card.step_type == "collect"
    assert session.workflow_state["current_step_index"] == 0
    executor.tool_gw.invoke.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["support", "billing", "other"])
async def test_documented_example_executes_each_branch(monkeypatch, choice):
    from server.api.workflows import _validate_workflow_steps
    from server.schemas.workflow import WorkflowCreate

    definition = json.loads((Path(__file__).parents[1] / "examples/support-triage.workflow.json").read_text(encoding="utf-8"))
    workflow = WorkflowCreate.model_validate(definition)
    assert not _validate_workflow_steps(workflow.steps)
    steps = [WorkflowStep(id=f"step-{i}", workflow_id="example", **step.model_dump()) for i, step in enumerate(workflow.steps)]
    probabilities = {value: 0.98 if value == choice else 0.01 for value in ["support", "billing", "other"]}
    monkeypatch.setattr(module, "decide", AsyncMock(return_value=DecisionResult("jev-test", choice, 0.95, probabilities, 1)))
    executor = WorkflowExecutor(None, None)
    executor.get_steps = AsyncMock(return_value=steps)
    session = ConversationSession(id="s", agent_id="a", tenant_id="t", collected_data={}, workflow_state={"workflow_id": "example", "current_step_index": 0})
    result = await executor.process_step(session, "", {"request": "Synthetic request"})
    if choice == "other":
        assert result.status == "escalated"
        assert session.workflow_state["status"] == "paused_for_review"
    else:
        assert result.status == "completed"
        assert session.collected_data["route"] == choice
