"""Template setup contracts exercised against the API and saved workflows."""

from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from server.api.workflows import _validate_workflow_steps
from server.config import settings
from server.db import async_session
from server.engine.tool_gateway import ToolGateway
from server.engine.workflow_executor import WorkflowExecutor
from server.main import app
from server.middleware.auth import create_jwt_token
from server.models.agent import Agent
from server.models.agent_skill import AgentSkill
from server.models.llm_config import LLMConfig
from server.models.session import ConversationSession
from server.models.skill import Skill
from server.models.user import User
from server.models.workflow import Workflow


@pytest.fixture
async def template_client(monkeypatch):
    monkeypatch.setattr(settings, "disable_auth", False)
    tenant = uuid4().hex
    user_id = uuid4().hex
    local_model = uuid4().hex
    foreign_model = uuid4().hex
    async with async_session() as db:
        db.add(User(id=user_id, username=user_id, password_hash="unused", tenant_id=tenant,
                    role="admin"))
        for model_id, owner in [(local_model, tenant), (foreign_model, uuid4().hex)]:
            db.add(LLMConfig(id=model_id, name="Offline model", tenant_id=owner,
                             provider="openai_compatible", base_url="http://127.0.0.1:9/v1",
                             model="offline-test", api_key=""))
        await db.commit()
    headers = {"Authorization": f"Bearer {create_jwt_token(user_id, tenant, 'admin')}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers=headers) as client:
        yield client, tenant, local_model, foreign_model


@pytest.mark.parametrize("model_kind", ["foreign", "missing"])
async def test_template_rejects_unavailable_model_before_creating_resources(template_client, model_kind):
    client, tenant, _, foreign_model = template_client
    response = await client.post(
        f"{settings.api_prefix}/agent-templates/repair_ticket/instantiate",
        json={"llm_config_id": foreign_model if model_kind == "foreign" else uuid4().hex},
    )
    assert response.status_code == 404
    async with async_session() as db:
        for model in (Agent, Workflow, Skill):
            assert await db.scalar(select(func.count()).select_from(model).where(
                model.tenant_id == tenant,
            )) == 0


@pytest.mark.parametrize("template_id", ["kb_support", "repair_ticket", "booking"])
async def test_template_wires_same_tenant_capabilities_and_model(template_client, template_id):
    client, tenant, local_model, _ = template_client
    response = await client.post(
        f"{settings.api_prefix}/agent-templates/{template_id}/instantiate",
        json={"llm_config_id": local_model},
    )
    assert response.status_code == 201, response.text
    agent = response.json()
    assert agent["llm_config_id"] == local_model
    assert agent["tenant_id"] == tenant
    async with async_session() as db:
        skills = list((await db.scalars(select(Skill).join(
            AgentSkill, AgentSkill.skill_id == Skill.id,
        ).where(
            AgentSkill.agent_id == agent["id"],
        ))).all())
        assert skills
        assert all(skill.tenant_id == tenant for skill in skills)
        assert all(skill.managed_by == f"agent:{agent['id']}" for skill in skills)
        for skill in skills:
            if skill.skill_type == "workflow":
                workflow = await db.get(Workflow, skill.execution_config["workflow_id"])
                assert workflow.tenant_id == tenant
                steps = await WorkflowExecutor(db, ToolGateway(db)).get_steps(workflow.id)
                assert _validate_workflow_steps(steps, require_steps=True) == []


@pytest.mark.parametrize("template_id", ["repair_ticket", "booking"])
async def test_default_intake_records_request_without_claiming_external_submission(template_client, template_id):
    client, tenant, local_model, _ = template_client
    response = await client.post(
        f"{settings.api_prefix}/agent-templates/{template_id}/instantiate",
        json={"llm_config_id": local_model},
    )
    assert response.status_code == 201
    agent_id = response.json()["id"]
    async with async_session() as db:
        skill = await db.scalar(select(Skill).join(
            AgentSkill, AgentSkill.skill_id == Skill.id,
        ).where(
            AgentSkill.agent_id == agent_id, Skill.skill_type == "workflow",
        ))
        workflow_id = skill.execution_config["workflow_id"]
        session = ConversationSession(
            agent_id=agent_id, user_id="offline-customer", tenant_id=tenant,
            collected_data={}, workflow_state={"workflow_id": workflow_id,
                                              "current_step_index": 0, "status": "active"},
        )
        db.add(session)
        await db.flush()
        executor = WorkflowExecutor(db, ToolGateway(db))
        executor._deliver_complete_webhook = AsyncMock()
        first = await executor.process_step(session, "")
        assert first.status == "waiting_input"
        assert first.card.step_type == "collect"
        form = {"customer_name": "测试用户", "phone": "13800000000"}
        if template_id == "repair_ticket":
            form["issue_description"] = "门锁无法打开"
        else:
            form.update({"matter": "设备检修", "appointment_time": "明天下午"})
        result = await executor.process_step(session, "", form_data=form)
        if template_id == "booking":
            assert result.status == "waiting_input"
            assert result.card.step_type == "confirm"
            result = await executor.process_step(session, "确认")
        assert result.status == "completed"
        assert "已记录" in result.message
        assert "提交成功" not in result.message
        assert "安排师傅" not in result.message
        executor._deliver_complete_webhook.assert_not_awaited()
        await db.commit()
        await db.refresh(session)
        assert session.collected_data == form
        assert session.workflow_state["status"] == "completed"
