"""Runtime resource ownership, independent of API CRUD and any developer database."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.db import Base
from server.engine.agent_runtime import AgentRuntime
from server.engine.knowledge_retriever import KnowledgeRetriever
from server.models.agent import Agent
from server.models.knowledge import KnowledgeChunk, KnowledgeSource
from server.models.llm_config import LLMConfig
from server.models.session import ConversationSession, Message
from server.models.skill import Skill
from server.models.tool import ToolDefinition
from server.models.workflow import Workflow
from server.schemas.invoke import InvokeRequest, InvokeResponse


@pytest.mark.asyncio
@pytest.mark.parametrize("step_type, status", [("complete", "completed"), ("human_review", "escalated")])
@pytest.mark.parametrize("prior_citation", [False, True])
async def test_workflow_receipt_is_not_rewritten_by_another_model_round(scoped_db, monkeypatch, step_type, status, prior_citation):
    from sqlalchemy import select
    from server.engine.agent_runtime import SkillToolResult
    from server.engine.llm_adapter import LLMResponse, ToolCallRequest
    from server.models.agent_skill import AgentSkill
    from server.models.workflow import WorkflowStep
    from server.engine import agent_runtime as runtime_module
    from server.schemas.invoke import Citation

    receipt = "Insufficient information. No ticket has been created or dispatched."
    scoped_db.add_all([
        Agent(id="receipt-agent", name="Receipt agent", tenant_id="t", enabled=True),
        Workflow(id="receipt-flow", name="Receipt workflow", tenant_id="t"),
        WorkflowStep(id="receipt-step", workflow_id="receipt-flow", name="Receipt", order=0,
                     step_type=step_type, prompt_template=receipt),
        Skill(id="receipt-skill", name="receipt", skill_type="workflow", tenant_id="t", enabled=True,
              execution_config={"workflow_id": "receipt-flow"}),
        AgentSkill(agent_id="receipt-agent", skill_id="receipt-skill", enabled=True),
    ])
    await scoped_db.commit()
    calls = []

    if prior_citation:
        build_tools = AgentRuntime._build_skill_tools

        async def with_knowledge(self, *args, **kwargs):
            definitions, handlers = await build_tools(self, *args, **kwargs)
            definitions.insert(0, {"type": "function", "function": {
                "name": "search_reference", "parameters": {"type": "object", "properties": {}},
            }})
            handlers["search_reference"] = AsyncMock(return_value=SkillToolResult(
                text="Reference data", citations=[Citation(
                    source_id="source", source_name="Reference", content_snippet="Unrelated retrieved article.",
                )],
            ))
            return definitions, handlers

        monkeypatch.setattr(AgentRuntime, "_build_skill_tools", with_knowledge)

    class Model:
        async def chat_with_tools(self, messages, tools):
            calls.append(messages)
            assert len(calls) == 1, "Workflow result must not be sent back for embellishment"
            tool_calls = []
            for tool in tools:
                name = tool["function"]["name"]
                raw = {"id": name, "type": "function", "function": {"name": name, "arguments": "{}"}}
                tool_calls.append(ToolCallRequest(name, name, {}, raw))
            return LLMResponse(content="", tool_calls=tool_calls)

    monkeypatch.setattr(runtime_module, "get_llm_adapter_for_agent", AsyncMock(return_value=Model()))
    monkeypatch.setattr(runtime_module, "emit_event", lambda *args, **kwargs: None)
    response = await AgentRuntime(scoped_db).invoke(InvokeRequest(agent_id="receipt-agent", message="Please run this workflow"))
    assert response.short_answer == receipt
    assert response.workflow_status == status
    assert response.escalated is (status == "escalated")
    assert len(calls) == 1
    assert bool(response.citations) is prior_citation
    saved_answer = await scoped_db.scalar(select(Message.content).where(
        Message.session_id == response.session_id, Message.role == "assistant",
    ))
    assert saved_answer == receipt


@pytest.fixture
async def scoped_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    await engine.dispose()


async def seed_knowledge(db):
    for source_id, tenant in [("selected", "a"), ("unselected", "a"), ("foreign", "b")]:
        db.add(KnowledgeSource(id=source_id, name=source_id, tenant_id=tenant, source_type="faq"))
        db.add(KnowledgeChunk(id=source_id, source_id=source_id, domain="default",
                              entity_key="refund", content=f"refund policy {source_id}"))
    await db.flush()


@pytest.mark.asyncio
async def test_all_retrieval_channels_filter_tenant_and_selected_sources(scoped_db):
    await seed_knowledge(scoped_db)
    calls = []

    class UntrustedIndex:
        def search(self, query, **kwargs):
            calls.append(kwargs)
            # Revalidation must also reject a stale or noncompliant index result.
            return [{"chunk_id": item, "score": 1.0} for item in ["foreign", "unselected", "selected"]]

    retriever = KnowledgeRetriever(scoped_db, UntrustedIndex(), tenant_id="a", source_ids=["selected", "foreign"])
    for results in [
        await retriever.fast_lookup("refund"),
        await retriever._keyword_search("refund", "default", 5),
        await retriever._vector_search("refund", "default", 5),
        (await retriever.retrieve("refund")).hits,
    ]:
        assert [hit.source_id for hit in results] == ["selected"]
    assert calls[0]["allowed_chunk_ids"] == {"selected"}


@pytest.mark.asyncio
async def test_empty_selected_source_list_does_not_expand_to_entire_tenant(scoped_db):
    await seed_knowledge(scoped_db)
    retriever = KnowledgeRetriever(scoped_db, tenant_id="a", source_ids=[])
    assert not (await retriever.retrieve("refund")).hits


@pytest.mark.asyncio
async def test_vector_hit_hydration_binds_only_returned_ids(scoped_db):
    from sqlalchemy import event

    scoped_db.add(KnowledgeSource(id="source", name="source", tenant_id="a", source_type="document"))
    await scoped_db.flush()
    scoped_db.add_all([
        KnowledgeChunk(id=f"chunk-{number}", source_id="source", domain="default", content="refund policy")
        for number in range(100)
    ])
    await scoped_db.flush()
    parameter_counts = []

    def record_parameters(connection, cursor, statement, parameters, context, executemany):
        parameter_counts.append(len(parameters))

    engine = scoped_db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record_parameters)
    try:
        index = SimpleNamespace(search=lambda *args, **kwargs: [
            {"chunk_id": f"chunk-{number}", "score": 0.9} for number in range(5)
        ])
        results = await KnowledgeRetriever(scoped_db, index, tenant_id="a")._vector_search(
            "refund", "default", 5,
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_parameters)

    assert len(results) == 5
    # Five result IDs and the tenant predicate, independent of corpus size.
    assert max(parameter_counts) <= 6


@pytest.mark.asyncio
async def test_knowledge_debug_search_is_tenant_scoped(scoped_db):
    from server.api.knowledge import search
    from server.schemas.knowledge import RetrievalRequest
    await seed_knowledge(scoped_db)
    response = await search(RetrievalRequest(query="refund", use_rag_channel=False), scoped_db, "b")
    assert {hit.source_id for hit in response.hits} == {"foreign"}


@pytest.mark.asyncio
async def test_tool_and_workflow_registration_rejects_foreign_resources(scoped_db):
    scoped_db.add_all([
        ToolDefinition(id="foreign-tool", name="foreign", tenant_id="b", enabled=True),
        Workflow(id="foreign-workflow", name="foreign", tenant_id="b"),
    ])
    await scoped_db.flush()
    runtime = AgentRuntime(scoped_db)
    assert await runtime._load_tools_as_functions(["foreign-tool"], "a") == ([], {})
    definitions = []
    await runtime._register_workflow_tool(
        Skill(id="s", name="s", tenant_id="a", skill_type="workflow"),
        {"workflow_id": "foreign-workflow"}, definitions, {}, lambda name: name,
        SimpleNamespace(tenant_id="a"), SimpleNamespace(),
    )
    assert definitions == []


@pytest.mark.asyncio
async def test_delegate_rejects_cross_tenant_target_before_invocation(scoped_db):
    target = Agent(id="b", name="b", tenant_id="b", enabled=True)
    scoped_db.add(target)
    await scoped_db.flush()
    runtime = AgentRuntime(scoped_db)
    runtime._get_history = AsyncMock(side_effect=AssertionError("must not read history"))
    handler = runtime._make_delegate_handler(
        SimpleNamespace(id="skill"), {"target_agent_id": "b"},
        SimpleNamespace(id="a", tenant_id="a"), SimpleNamespace(id="session"), SimpleNamespace(),
    )
    result = await handler({"message": "do work"})
    assert result.skill_info is None
    runtime._get_history.assert_not_called()


@pytest.mark.asyncio
async def test_nested_delegation_inherits_server_chain_and_stops_cycle(scoped_db, monkeypatch):
    for agent_id in ["a", "b", "c", "d", "e"]:
        scoped_db.add(Agent(id=agent_id, name=agent_id, tenant_id="tenant", enabled=True))
    await scoped_db.flush()
    seen = []
    audit = SimpleNamespace(log=lambda *args, **kwargs: None)

    async def delegated_invoke(self, req):
        seen.append((req.agent_id, self._delegation_ancestors))
        agent = await self._load_agent(req.agent_id)
        session = await self._get_or_create_session(req, agent)
        assert session.delegation_chain == list(self._delegation_ancestors)
        self._get_history = AsyncMock(return_value=[])
        target_id = {"b": "c", "c": "d", "d": "e"}[req.agent_id]
        result = await self._make_delegate_handler(
            SimpleNamespace(id="skill"), {"target_agent_id": target_id}, agent, session, audit,
        )({"message": "delegate"})
        return InvokeResponse(session_id=session.id, trace_id="trace", short_answer=result.text)

    monkeypatch.setattr(AgentRuntime, "invoke", delegated_invoke)
    runtime = AgentRuntime(scoped_db)
    runtime._get_history = AsyncMock(return_value=[])
    source = await runtime._load_agent("a")
    session = SimpleNamespace(id="root", shared_context={})
    result = await runtime._make_delegate_handler(
        SimpleNamespace(id="skill"), {"target_agent_id": "b"}, source, session, audit,
    )({"message": "delegate"})
    assert seen == [("b", ("a",)), ("c", ("a", "b")), ("d", ("a", "b", "c"))]
    assert "3" in result.text
    runtime._delegation_ancestors = ("b",)
    seen.clear()
    await runtime._make_delegate_handler(
        SimpleNamespace(id="skill"), {"target_agent_id": "b"}, source, session, audit,
    )({"message": "delegate"})
    assert seen == []


@pytest.mark.asyncio
async def test_public_parent_id_cannot_forge_delegation_chain(scoped_db):
    agent = Agent(id="a", name="a", tenant_id="a", enabled=True)
    scoped_db.add(agent)
    await scoped_db.flush()
    session = await AgentRuntime(scoped_db)._get_or_create_session(
        InvokeRequest(agent_id="a", message="hi", parent_session_id="foreign-parent"), agent,
    )
    assert session.parent_session_id is None
    assert session.delegation_chain is None


@pytest.mark.asyncio
async def test_over_budget_runtime_never_calls_model_or_action_fallback(monkeypatch):
    from server.engine.llm_adapter import LLMResponse
    import server.engine.agent_runtime as runtime_module

    runtime = AgentRuntime(None)
    runtime._load_agent_skills = AsyncMock(return_value=[])
    runtime._get_history = AsyncMock(return_value=[])
    runtime._get_persisted_summary = AsyncMock(return_value=None)
    runtime._get_context_history = AsyncMock(return_value=[])
    runtime._execute_action_fallback = AsyncMock()
    llm = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="must not run")))
    monkeypatch.setattr(runtime_module, "get_llm_adapter_for_agent", AsyncMock(return_value=llm))
    monkeypatch.setattr(runtime_module, "MAX_INPUT_TOKENS", 100)
    events = []
    audit = SimpleNamespace(start_timer=lambda *args: None,
                            log=lambda name, **kwargs: events.append(name))
    result = await runtime._invoke_conversational(
        Agent(id="a", name="a", tenant_id="a", system_prompt="instructions " * 1000),
        SimpleNamespace(id="s", workflow_state=None),
        InvokeRequest(agent_id="a", message="hello", expand=True), "trace", audit,
    )
    assert result.metadata["error_detail"] == "context_budget_exceeded"
    assert "context_budget_exceeded" in events
    llm.chat.assert_not_called()
    runtime._execute_action_fallback.assert_not_called()


@pytest.mark.parametrize("hnsw", [True, False])
def test_faiss_scope_does_not_lose_authorized_result_below_foreign_top_k(hnsw):
    import numpy as np
    from server.engine.vector_store import VectorStoreManager
    faiss = pytest.importorskip("faiss")

    manager = object.__new__(VectorStoreManager)
    manager._index = faiss.IndexHNSWFlat(2, 16, faiss.METRIC_INNER_PRODUCT) if hnsw else faiss.IndexFlatIP(2)
    manager._index.add(np.array([[1, 0], [.9, .1], [.8, .2], [.7, .3], [0, 1]], dtype=np.float32))
    manager._is_hnsw = hnsw
    manager._ids = ["foreign1", "foreign2", "foreign3", "foreign4", "selected"]
    manager._domains = ["default"] * 5
    manager._embedding = SimpleNamespace(encode=lambda *args, **kwargs: np.array([[1.0, 0.0]]))
    results = manager.search("refund", top_k=1, allowed_chunk_ids={"selected"})
    assert [item["chunk_id"] for item in results] == ["selected"]
    assert manager.search("refund", allowed_chunk_ids=set()) == []


def test_pgvector_scope_is_bound_in_query_before_limit():
    import numpy as np
    from server.engine.pgvector_store import PgVectorStore

    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, parameters=None):
            calls.append((str(statement), parameters))
            return SimpleNamespace(fetchall=lambda: [])

    store = object.__new__(PgVectorStore)
    store._table_name = "vector_embeddings"
    store._engine = SimpleNamespace(begin=lambda: Connection())
    store._embedding = SimpleNamespace(encode=lambda *args, **kwargs: np.array([[1.0]]))
    assert store.search("refund", domain="default", allowed_chunk_ids={"selected"}) == []
    sql, params = calls[-1]
    assert "chunk_id = ANY(:allowed_ids)" in sql
    assert sql.index("ANY(:allowed_ids)") < sql.index("LIMIT")
    assert params["allowed_ids"] == ["selected"]
    assert params["domain"] == "default"


@pytest.mark.asyncio
async def test_context_keeps_unsummarized_gap_when_background_summary_lags(scoped_db):
    from datetime import datetime, timedelta
    session = ConversationSession(id="s", agent_id="a", user_id="u", tenant_id="a",
                                  context={"summary": "first two messages", "summarized_upto": 2})
    scoped_db.add(session)
    for number in range(10):
        scoped_db.add(Message(id=str(number), session_id="s", role="user" if number % 2 == 0 else "assistant",
                              content=f"message-{number}", created_at=datetime(2026, 1, 1) + timedelta(seconds=number)))
    await scoped_db.flush()
    runtime = AgentRuntime(scoped_db)
    history = await runtime._get_context_history(session)
    assert [row.content for row in history] == [f"message-{number}" for number in range(2, 10)]
    session.context = {}
    assert len(await runtime._get_context_history(session)) == 10


async def seed_llm_configs(db):
    for tenant in ["a", "b"]:
        db.add(LLMConfig(id=f"llm-{tenant}", name=f"config-{tenant}", tenant_id=tenant,
                         provider="openai_compatible", base_url=f"https://{tenant}.example/v1",
                         api_key=f"key-{tenant}", model=f"model-{tenant}", is_default=True))
    await db.flush()


@pytest.mark.asyncio
async def test_runtime_foreign_llm_reference_uses_own_tenant_default(scoped_db):
    from server.engine.llm_adapter import get_llm_adapter_for_agent
    await seed_llm_configs(scoped_db)
    agent = Agent(id="a", name="a", tenant_id="a", llm_config_id="llm-b")
    adapter = await get_llm_adapter_for_agent(agent, scoped_db)
    assert adapter.base_url == "https://a.example/v1"
    assert adapter.model == "model-a"
    assert adapter.api_key == "key-a"
    agent.llm_config_id = "llm-a"
    assert (await get_llm_adapter_for_agent(agent, scoped_db)).model == "model-a"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "bulk"])
@pytest.mark.parametrize("config_id", ["llm-b", "missing-config"])
async def test_agent_api_rejects_foreign_or_missing_llm_binding(scoped_db, operation, config_id):
    from fastapi import HTTPException
    from server.api.agents import create_agent, update_agent, bulk_update_agents
    from server.schemas.agent import AgentCreate, AgentUpdate
    await seed_llm_configs(scoped_db)
    agent = Agent(id="a", name="a", tenant_id="a", llm_config_id="llm-a")
    scoped_db.add(agent)
    await scoped_db.flush()
    with pytest.raises(HTTPException) as caught:
        if operation == "create":
            await create_agent(AgentCreate(name="new", llm_config_id=config_id), "a", scoped_db)
        elif operation == "update":
            await update_agent("a", AgentUpdate(llm_config_id=config_id), "a", scoped_db)
        else:
            await bulk_update_agents({"agent_ids": ["a"], "updates": {"llm_config_id": config_id}}, "a", scoped_db)
    assert caught.value.status_code == 404
    assert agent.llm_config_id == "llm-a"


@pytest.mark.asyncio
async def test_owned_llm_binding_can_be_created_and_cleared(scoped_db):
    from server.api.agents import create_agent, update_agent, bulk_update_agents
    from server.schemas.agent import AgentCreate, AgentUpdate
    await seed_llm_configs(scoped_db)
    agent = await create_agent(AgentCreate(name="new", llm_config_id="llm-a"), "a", scoped_db)
    assert agent.llm_config_id == "llm-a"
    await update_agent(agent.id, AgentUpdate(llm_config_id=None), "a", scoped_db)
    assert agent.llm_config_id is None
    result = await bulk_update_agents({"agent_ids": [agent.id], "updates": {"llm_config_id": "llm-a"}}, "a", scoped_db)
    assert result == {"updated": 1}
    assert agent.llm_config_id == "llm-a"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["knowledge", "workflows", "tools"])
@pytest.mark.parametrize("owner", ["foreign", "missing"])
async def test_capability_api_rejects_foreign_or_missing_resource_before_mutation(scoped_db, kind, owner):
    from fastapi import HTTPException
    from sqlalchemy import select
    from server.api.agent_capabilities import CapabilitiesPayload, update_capabilities
    scoped_db.add_all([
        Agent(id="a", name="a", tenant_id="a"),
        KnowledgeSource(id="foreign", name="SECRET SOURCE", tenant_id="b", source_type="faq"),
        ToolDefinition(id="foreign", name="SECRET TOOL", tenant_id="b"),
        Workflow(id="foreign", name="SECRET WORKFLOW", tenant_id="b"),
    ])
    await scoped_db.flush()
    capability = {"source_ids": [owner]} if kind == "knowledge" else (
        {"workflow_id": owner} if kind == "workflows" else {"tool_ids": [owner]}
    )
    with pytest.raises(HTTPException) as caught:
        await update_capabilities("a", CapabilitiesPayload(**{kind: [capability]}), "a", scoped_db)
    assert caught.value.status_code == 404
    assert "SECRET" not in str(caught.value.detail)
    assert not (await scoped_db.execute(select(Skill))).scalars().all()


@pytest.mark.asyncio
async def test_owned_capabilities_still_bind_with_resource_descriptions(scoped_db):
    from sqlalchemy import select
    from server.api.agent_capabilities import CapabilitiesPayload, update_capabilities
    scoped_db.add_all([
        Agent(id="a", name="a", tenant_id="a"),
        KnowledgeSource(id="source", name="OWN SOURCE", tenant_id="a", source_type="faq"),
        ToolDefinition(id="tool", name="OWN TOOL", tenant_id="a"),
        Workflow(id="workflow", name="OWN WORKFLOW", tenant_id="a"),
    ])
    await scoped_db.flush()
    result = await update_capabilities("a", CapabilitiesPayload(
        knowledge=[{"source_ids": ["source"]}], workflows=[{"workflow_id": "workflow"}],
        tools=[{"tool_ids": ["tool"]}],
    ), "a", scoped_db)
    assert result["knowledge"][0]["source_ids"] == ["source"]
    descriptions = " ".join(skill.description for skill in (await scoped_db.execute(select(Skill))).scalars().all())
    assert all(name in descriptions for name in ["OWN SOURCE", "OWN TOOL", "OWN WORKFLOW"])
