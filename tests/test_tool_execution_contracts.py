"""Offline regressions for workflow inputs and exact tool-to-handler binding."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.db import Base
from server.engine.agent_runtime import AgentRuntime
from server.engine.business_data import tool_input_schema
from server.engine import llm_adapter
from server.engine.tool_gateway import ToolGateway
from server.engine.workflow_executor import WorkflowExecutor
from server.models.agent import Agent
from server.models.business_data import BusinessDataSource, BusinessRecord
from server.models.llm_config import LLMConfig
from server.models.session import ConversationSession
from server.models.skill import Skill
from server.models.tool import ToolDefinition
from server.models.workflow import Workflow, WorkflowStep


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def workflow_session(db, tool, collected, input_mapping):
    session = ConversationSession(
        id="session", agent_id="agent", user_id="requester", tenant_id="tenant",
        collected_data=collected,
        workflow_state={"workflow_id": "workflow", "current_step_index": 0,
                        "idempotency_key": "submission"},
    )
    db.add_all([
        tool,
        Workflow(id="workflow", name="Workflow", tenant_id="tenant"),
        WorkflowStep(id="call", workflow_id="workflow", name="Lookup", order=0,
                     step_type="tool_call", tool_id=tool.id,
                     tool_config={"input_mapping": input_mapping}),
        session,
    ])
    await db.flush()
    return session


@pytest.mark.parametrize("schema_mode", ["strict", "declared", "permissive", "none"])
async def test_workflow_idempotency_respects_tool_body_schema(db, monkeypatch, schema_mode):
    schema = {"type": "object", "properties": {"query": {"type": "string"}},
              "required": ["query"], "additionalProperties": False}
    if schema_mode == "declared":
        schema["properties"]["idempotency_key"] = {"type": "string"}
        schema["required"].append("idempotency_key")
    elif schema_mode == "permissive":
        schema.pop("additionalProperties")
    elif schema_mode == "none":
        schema = None
    tool = ToolDefinition(id="tool", name="Lookup", tenant_id="tenant", input_schema=schema,
                          endpoint="https://unused.invalid/query", max_retries=0)
    session = await workflow_session(db, tool, {"query": "A-1"}, {"query": "query"})
    calls = []

    async def capture_http(self, definition, input_data, extra_headers=None):
        calls.append((dict(input_data), dict(extra_headers or {})))
        return {"found": True}

    # Keep the real gateway schema validator; only the network boundary is fake.
    monkeypatch.setattr(ToolGateway, "_call_http_tool", capture_http)
    executor = WorkflowExecutor(db, ToolGateway(db))
    for _ in range(2):
        result = await executor.process_step(session, "")
        assert result.status == "completed", result.message
        session.workflow_state = {**session.workflow_state, "current_step_index": 0}
    assert len(calls) == 2
    expected = {"query": "A-1"}
    if schema_mode != "strict":
        expected["idempotency_key"] = "submission:call"
    assert calls == [(expected, {"X-Idempotency-Key": "submission:call"})] * 2
    assert session.collected_data["_tool_result_Lookup"] == {"found": True}


async def test_workflow_can_execute_generated_strict_data_query(db):
    source = BusinessDataSource(
        id="source", tool_id="query-tool", tenant_id="tenant", name="Orders", kind="table",
        fields=[{"name": "order_id", "type": "string"}], filter_fields=["order_id"],
        required_filters=["order_id"], max_rows=5, enabled=True,
    )
    tool = ToolDefinition(id="query-tool", name="Orders query", tenant_id="tenant",
                          category="data_query", endpoint=source.id, max_retries=0,
                          input_schema=tool_input_schema(source))
    session = await workflow_session(
        db, tool, {"filters": {"order_id": "A-1"}}, {"filters": "filters"},
    )
    db.add(source)
    await db.flush()
    db.add(BusinessRecord(source_id=source.id, tenant_id="tenant", values={"order_id": "A-1"}))
    await db.flush()

    result = await WorkflowExecutor(db, ToolGateway(db)).process_step(session, "")

    assert result.status == "completed", result.message
    output = session.collected_data["_tool_result_Lookup"]
    assert output["rows"] == [{"order_id": "A-1"}]
    assert output["row_count"] == 1
    assert output["source_id"] == source.id


@pytest.mark.parametrize("separate_skills", [False, True])
@pytest.mark.parametrize("display_names", [
    ("check-status", "check status", "check_status_2"),
    ("a" * 62 + "-b", "a" * 62 + " b", "a" * 62 + "_b_2"),
    ("status_" * 12 + "read", "status_" * 12 + "write", "status_" * 12 + "history"),
], ids=["short", "64-character-collision", "long-common-prefix"])
async def test_colliding_function_names_keep_their_own_execution_target(
    db, monkeypatch, separate_skills, display_names,
):
    tools = [
        ToolDefinition(id="read", name=display_names[0], description="Read current status",
                       tenant_id="tenant"),
        ToolDefinition(id="write", name=display_names[1], description="Change current status",
                       tenant_id="tenant"),
        ToolDefinition(id="suffix", name=display_names[2], description="Read status history",
                       tenant_id="tenant"),
    ]
    db.add_all(tools)
    await db.flush()
    groups = [[tool.id for tool in tools]]
    if separate_skills:
        groups = [[tools[0].id], [tool.id for tool in tools[1:]]]
    skills = [Skill(id=f"skill-{i}", name=f"Skill {i}", tenant_id="tenant", skill_type="tool_call",
                    execution_config={"tool_ids": ids}) for i, ids in enumerate(groups)]
    calls = []

    async def capture_invoke(self, tool_id, input_data=None, extra_headers=None):
        calls.append((tool_id, input_data))
        return {"executed_tool_id": tool_id}

    monkeypatch.setattr(ToolGateway, "invoke", capture_invoke)
    definitions, handlers = await AgentRuntime(db)._build_skill_tools(
        Agent(id="agent", name="Agent", tenant_id="tenant"), SimpleNamespace(), None,
        preloaded_skills=skills,
    )
    names = [definition["function"]["name"] for definition in definitions]
    assert len(names) == len(set(names)) == len(handlers) == len(tools)
    assert all(1 <= len(name) <= 64 for name in names)
    expected_ids = {tool.description: tool.id for tool in tools}
    for definition in definitions:
        function = definition["function"]
        expected_id = expected_ids[function["description"]]
        response = await handlers[function["name"]]({"query": "A-1"})
        assert json.loads(response.text) == {"executed_tool_id": expected_id}
        assert calls[-1] == (expected_id, {"query": "A-1"})
    assert {tool_id for tool_id, _ in calls} == {tool.id for tool in tools}


async def collect_session(db, *, explicit_model=True, agent_tenant="tenant", enabled=True):
    db.add_all([
        LLMConfig(id="chosen", name="Chosen model", tenant_id="tenant", provider="local",
                  base_url="http://127.0.0.1:9/v1", model="chosen-model"),
        LLMConfig(id="default", name="Tenant default", tenant_id="tenant", provider="local",
                  base_url="http://127.0.0.1:9/v1", model="default-model", is_default=True),
        LLMConfig(id="foreign", name="Foreign default", tenant_id="other", provider="local",
                  base_url="http://127.0.0.1:9/v1", model="foreign-model", is_default=True),
        Agent(id="agent", name="Agent", tenant_id=agent_tenant, enabled=enabled,
              llm_config_id="chosen" if explicit_model else None),
        Workflow(id="workflow", name="Collect", tenant_id="tenant"),
    ])
    step = WorkflowStep(
        id="collect", workflow_id="workflow", name="Collect issue", order=0, step_type="collect",
        fields=[{"name": "issue", "label": "Issue", "required": True, "llm_validate": True,
                 "llm_validate_prompt": "Check that this describes a repair issue."}],
    )
    session = ConversationSession(
        id="session", agent_id="agent", tenant_id="tenant", user_id="requester", collected_data={},
        workflow_state={"workflow_id": "workflow", "current_step_index": 0},
    )
    db.add_all([step, session])
    await db.flush()
    return session, step


@pytest.mark.parametrize("explicit_model", [False, True])
@pytest.mark.parametrize("structured", [False, True])
async def test_collection_and_validation_use_session_agents_model(db, monkeypatch, explicit_model, structured):
    session, _ = await collect_session(db, explicit_model=explicit_model)
    models = []

    async def chat(adapter, messages, **kwargs):
        models.append(adapter.model)
        content = '{"issue":"Water leak"}' if len(models) == 1 and not structured else "OK"
        return SimpleNamespace(content=content)

    def reject_global(**kwargs):
        raise AssertionError("Must use the tenant's configured model")

    monkeypatch.setattr(llm_adapter.LLMAdapter, "chat", chat)
    monkeypatch.setattr(llm_adapter, "get_llm_adapter", reject_global)
    result = await WorkflowExecutor(db, ToolGateway(db)).process_step(
        session, "There is a water leak", {"issue": "Water leak"} if structured else None,
    )

    assert result.status == "completed", result.message
    assert session.collected_data["issue"] == "Water leak"
    assert models == ["chosen-model" if explicit_model else "default-model"] * (1 if structured else 2)


@pytest.mark.parametrize("failure", ["provider", "empty", "missing_prompt", "missing_agent", "foreign_agent", "disabled_agent"])
async def test_required_semantic_validation_unavailable_blocks_following_tool(db, monkeypatch, failure):
    session, step = await collect_session(
        db, agent_tenant="other" if failure == "foreign_agent" else "tenant",
        enabled=failure != "disabled_agent",
    )
    if failure == "missing_agent":
        session.agent_id = "missing"
    if failure == "missing_prompt":
        step.fields = [{**step.fields[0], "llm_validate_prompt": ""}]
    db.add_all([
        ToolDefinition(id="write", name="Create repair ticket", tenant_id="tenant"),
        WorkflowStep(id="write", workflow_id="workflow", name="Create ticket", order=1,
                     step_type="tool_call", tool_id="write"),
    ])
    await db.flush()
    calls = []

    async def chat(adapter, messages, **kwargs):
        calls.append(adapter.model)
        if failure == "provider":
            raise RuntimeError("Synthetic provider timeout")
        return SimpleNamespace(content="")

    def reject_global(**kwargs):
        raise AssertionError("Must not fall back to an unrelated global model")

    monkeypatch.setattr(llm_adapter.LLMAdapter, "chat", chat)
    monkeypatch.setattr(llm_adapter, "get_llm_adapter", reject_global)
    gateway = SimpleNamespace(invoke=AsyncMock(return_value={"created": True}))
    result = await WorkflowExecutor(db, gateway).process_step(
        session, "", {"issue": "Water leak"},
    )

    assert result.status == "waiting_input", result.message
    assert "Issue" in result.message
    assert "issue" not in session.collected_data
    assert session.workflow_state["current_step_index"] == 0
    gateway.invoke.assert_not_called()
    assert calls == (["chosen-model"] if failure in {"provider", "empty"} else [])
