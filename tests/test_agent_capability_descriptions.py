from types import SimpleNamespace

import pytest

from server.api.agent_capabilities import _skill_to_capability
from server.engine.agent_runtime import AgentRuntime


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDb:
    def __init__(self, value):
        self.value = value

    async def execute(self, _statement):
        return _Result(self.value)


def _unique(name: str) -> str:
    return name


def test_capabilities_get_returns_knowledge_override_not_generated_default():
    skill = SimpleNamespace(
        skill_type="knowledge_qa",
        description="Search knowledge base (property_support) for relevant information",
        execution_config={
            "domain": "property_support",
            "knowledge_source_ids": ["source-1"],
        },
        trigger_config={},
    )

    cap_type, data = _skill_to_capability(skill)

    assert cap_type == "knowledge"
    assert data["source_ids"] == ["source-1"]
    assert data["description"] == ""


@pytest.mark.asyncio
async def test_workflow_tool_description_uses_workflow_resource_description_by_default():
    workflow = SimpleNamespace(
        id="workflow-1",
        tenant_id="default",
        name="Repair Ticket",
        description="Start this workflow when the user wants to report a home repair issue.",
    )
    runtime = AgentRuntime(db=_FakeDb(workflow))
    skill = SimpleNamespace(
        name="[auto] Property Agent - workflow",
        description="Auto-managed workflow skill",
        trigger_config={},
    )
    tool_defs = []

    await runtime._register_workflow_tool(
        skill=skill,
        config={"workflow_id": workflow.id},
        tool_defs=tool_defs,
        handler_map={},
        unique_name=_unique,
        session=SimpleNamespace(tenant_id="default"),
        audit=SimpleNamespace(),
    )

    description = tool_defs[0]["function"]["description"]
    assert "Repair Ticket" in description
    assert "home repair issue" in description
    assert "Auto-managed workflow skill" not in description


@pytest.mark.asyncio
async def test_workflow_tool_description_appends_agent_specific_override():
    workflow = SimpleNamespace(
        id="workflow-1",
        tenant_id="default",
        name="Repair Ticket",
        description="Start this workflow when the user wants to report a home repair issue.",
    )
    runtime = AgentRuntime(db=_FakeDb(workflow))
    skill = SimpleNamespace(
        name="[auto] Property Agent - workflow",
        description="Auto-managed workflow skill",
        trigger_config={
            "trigger_description": "Only use this for residential properties.",
        },
    )
    tool_defs = []

    await runtime._register_workflow_tool(
        skill=skill,
        config={"workflow_id": workflow.id},
        tool_defs=tool_defs,
        handler_map={},
        unique_name=_unique,
        session=SimpleNamespace(tenant_id="default"),
        audit=SimpleNamespace(),
    )

    description = tool_defs[0]["function"]["description"]
    assert "home repair issue" in description
    assert "Only use this for residential properties" in description


@pytest.mark.asyncio
async def test_http_tool_description_appends_agent_specific_override():
    runtime = AgentRuntime(db=None)

    async def fake_load_tools(tool_ids, tenant_id):
        assert tool_ids == ["tool-1"]
        assert tenant_id == "default"
        return (
            [
                {
                    "type": "function",
                    "function": {
                        "name": "create_work_order",
                        "description": "Create a work order in the customer's CRM.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    },
                }
            ],
            {
                "create_work_order": SimpleNamespace(id="tool-1"),
            },
        )

    runtime._load_tools_as_functions = fake_load_tools
    runtime._make_http_tool_handler = lambda _tool_definition, _audit: (lambda **_kwargs: None)

    skill = SimpleNamespace(
        tenant_id="default",
        trigger_config={
            "trigger_description": "Only after the user provides address and issue type.",
        },
    )
    tool_defs = []

    await runtime._register_http_tools(
        skill=skill,
        config={"tool_ids": ["tool-1"]},
        tool_defs=tool_defs,
        handler_map={},
        unique_name=_unique,
        audit=SimpleNamespace(),
    )

    description = tool_defs[0]["function"]["description"]
    assert "customer's CRM" in description
    assert "address and issue type" in description
