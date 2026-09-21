"""Bounded live verification against MiniMax and Jev using synthetic support cases.

Run from the repo root after setting MINIMAX_API_KEY and TYPESAFE_API_KEY:
    python scripts/verify_agent_workflows.py
    python scripts/verify_agent_workflows.py --runtime-only

Creates a temporary SQLite database, never the application's configured DB.
Uses the real adapters/runtime/executor. Workflow terminal receipts are local
test outputs; this does not create tickets or contact a business system.
At most six MiniMax and twelve TypeSafe HTTP requests are allowed per run.
Only sanitized observations are saved to data/verification/live-agent-workflows.json.
"""
from __future__ import annotations

import asyncio
import argparse
import gc
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
MINIMAX_URL = "https://api.minimaxi.com/v1/chat/completions"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
LIMITS = {"minimax": 6, "jev": 12}
REPAIR_RECEIPT = "TEST_REPAIR_RECEIPT: 已归入维修分支，尚未创建外部工单。"

DECISION = {
    "instructions": (
        "Route a customer support issue using only the description. Choose repair for physical "
        "maintenance, leaks, or broken fixtures; billing for charges, invoices, or refunds. "
        "If both categories are equally requested, information is insufficient, or the request "
        "is unrelated, choose other. Do not infer details that are absent."
    ),
    "input_fields": ["description"],
    "choices": [
        {"value": "repair", "description": "Physical maintenance, leaking pipes, broken fixtures."},
        {"value": "billing", "description": "Incorrect charges, duplicate payment, invoices, refunds."},
        {"value": "other", "description": "Unrelated, insufficient information, or equally mixed intents."},
    ],
    "result_key": "route",
    "min_confidence": 0.8,
    "min_probability": 0.8,
}

CASES = [
    ("repair_zh", "厨房水管一直漏水，需要安排维修。", "repair"),
    ("billing_en", "I was charged twice for September. Please check the duplicate charge.", "billing"),
    ("repair_en", "The bathroom pipe is leaking. Please arrange a plumber.", "repair"),
    ("billing_zh", "九月账单重复扣费，请帮我核对。", "billing"),
    ("no_match", "请推荐一首爵士乐。", "other"),
    ("mixed", "The kitchen pipe is leaking and I was charged twice. Both issues matter equally.", "other"),
    ("missing_input", None, "missing_input"),
]


def plain_chat_passed(answer: str, metadata: dict, calls: list[dict]) -> bool:
    """Require provider evidence so a canned runtime error cannot pass the probe."""
    return bool(answer) and not metadata.get("degraded") and not metadata.get("error_detail") and any(
        call.get("provider") == "minimax" and call.get("status_code") == 200 and bool(call.get("actual_model"))
        for call in calls
    )


class RequestRecorder:
    """Observe real HTTP calls and enforce a hard spend ceiling without logging secrets."""

    def __init__(self):
        self.counts = {provider: 0 for provider in LIMITS}
        self.calls: list[dict] = []
        self._post = httpx.AsyncClient.post

    async def post(self, client, url, *args, **kwargs):
        provider = {MINIMAX_URL: "minimax", JEV_URL: "jev"}.get(str(url))
        if provider is None:
            raise RuntimeError("verification_unexpected_network_destination")
        if self.counts[provider] >= LIMITS[provider]:
            raise RuntimeError("verification_request_limit")
        self.counts[provider] += 1
        request = kwargs.get("json") or {}
        record = {"provider": provider, "requested_model": request.get("model"),
                  "requested_max_tokens": request.get("max_tokens")}
        self.calls.append(record)
        start = time.perf_counter()
        try:
            response = await self._post(client, url, *args, **kwargs)
            record["status_code"] = response.status_code
            try:
                body = response.json()
            except ValueError:
                body = {}
            if isinstance(body, dict):
                record["actual_model"] = body.get("model")
                record["response_keys"] = sorted(body)
                usage = body.get("usage") or {}
                record["usage"] = {name: usage.get(name) for name in (
                    "prompt_tokens", "completion_tokens", "total_tokens",
                ) if isinstance(usage.get(name), (int, float))}
                if provider == "minimax" and body.get("choices"):
                    choice = body["choices"][0]
                    record["finish_reason"] = choice.get("finish_reason")
                    record["tool_names"] = [
                        call.get("function", {}).get("name")
                        for call in choice.get("message", {}).get("tool_calls", []) or []
                    ]
                if provider == "jev":
                    answer = (body.get("answers") or {}).get("route", {})
                    record["decision_response"] = {name: answer.get(name) for name in (
                        "type", "choice", "confidence", "probabilities",
                    )}
            return response
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)


async def verify(recorder: RequestRecorder, report: dict, *, runtime_only: bool = False) -> dict:
    from sqlalchemy import select
    from server.db import Base, async_session, engine
    from server.engine.agent_runtime import AgentRuntime
    from server.engine.audit_logger import AuditLogger
    from server.engine.tool_gateway import ToolGateway
    from server.engine.workflow_executor import WorkflowExecutor
    from server.models import Agent, AgentSkill, AuditTrace, ConversationSession, Skill, Workflow, WorkflowStep
    from server.schemas.invoke import InvokeRequest

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    report.update({"started_at": datetime.now(timezone.utc).isoformat(),
                   "business_effects": "No external business tools; local terminal receipts only.",
                   "assertion_revision": 3,
                   "scope": "runtime_only" if runtime_only else "runtime_and_labeled_decisions",
                   "decision_policy": DECISION, "observations": [], "calls": recorder.calls})
    async with async_session() as db:
        db.add_all([
            Agent(id="chat-agent", name="Live chat probe", tenant_id="verification", enabled=True,
                  system_prompt="Answer briefly in the user's language. Do not invent completed actions."),
            Agent(id="support-agent", name="Live support probe", tenant_id="verification", enabled=True,
                  system_prompt="For support requests call the service triage workflow and put the user's full issue in reason. Reply briefly based on its result."),
            Workflow(id="support-flow", name="Service triage", tenant_id="verification",
                     description="Route repair or billing support requests. Include the complete issue in reason."),
            Skill(id="support-skill", name="service_triage", skill_type="workflow", tenant_id="verification",
                  enabled=True, execution_config={"workflow_id": "support-flow"}),
            AgentSkill(agent_id="support-agent", skill_id="support-skill", enabled=True),
        ])
        steps = [
            WorkflowStep(id="collect", workflow_id="support-flow", order=0, name="Describe issue", step_type="collect",
                         prompt_template="请说明需要处理的问题。",
                         fields=[{"name": "description", "label": "问题描述", "field_type": "text", "required": True}]),
            WorkflowStep(id="decision", workflow_id="support-flow", order=1, name="Classify request", step_type="decision",
                         tool_config=DECISION, fallback_step_id="clarify", next_step_rules=[
                             {"condition": {"field": "route", "op": "eq", "value": "repair"}, "goto_step": "repair"},
                             {"condition": {"field": "route", "op": "eq", "value": "billing"}, "goto_step": "billing"},
                             {"condition": None, "goto_step": "clarify"},
                         ]),
            WorkflowStep(id="repair", workflow_id="support-flow", order=2, name="Repair receipt", step_type="complete",
                         prompt_template=REPAIR_RECEIPT),
            WorkflowStep(id="billing", workflow_id="support-flow", order=3, name="Billing receipt", step_type="complete",
                         prompt_template="TEST_BILLING_RECEIPT: 已归入账单分支，尚未修改账单。"),
            WorkflowStep(id="clarify", workflow_id="support-flow", order=4, name="Clarify request", step_type="collect",
                         prompt_template="请补充信息，并选择这次优先处理的一个问题。",
                         fields=[{"name": "description", "label": "问题描述", "required": True}],
                         next_step_rules=[{"condition": None, "goto_step": "decision"}]),
        ]
        db.add_all(steps)
        await db.commit()
        step_ids = [step.id for step in steps]

        # Plain conversation and real native workflow selection/extraction.
        for name, agent_id, message in [
            ("plain_chat", "chat-agent", "请用一句话解释：客服为什么要先核实问题再处理？"),
            ("native_workflow", "support-agent", "请启动客服分流流程。问题描述：厨房水管一直漏水，需要安排维修。"),
        ]:
            start = time.perf_counter()
            call_start = len(recorder.calls)
            observation = {"case": name, "kind": "agent_runtime", "input": message}
            try:
                response = await asyncio.wait_for(AgentRuntime(db).invoke(InvokeRequest(
                    agent_id=agent_id, user_id="synthetic-user", message=message,
                )), timeout=180)
                trace_ids = [response.trace_id]
                metadata = response.metadata or {}
                tool_calls = metadata.get("tool_calls", [])
                if name == "native_workflow" and response.workflow_status == "waiting_input" and recorder.counts["minimax"] < LIMITS["minimax"] - 1:
                    # If the first call legitimately asks for a field, give a
                    # natural-language value through the actual runtime again.
                    response = await asyncio.wait_for(AgentRuntime(db).invoke(InvokeRequest(
                        agent_id=agent_id, user_id="synthetic-user", session_id=response.session_id,
                        message="厨房水管一直漏水，需要安排维修。",
                    )), timeout=180)
                    trace_ids.append(response.trace_id)
                traces = (await db.execute(select(AuditTrace).where(AuditTrace.trace_id.in_(trace_ids)))).scalars().all()
                decisions = [trace.workflow_meta for trace in traces if trace.event_type == "workflow_decision"]
                fields = response.workflow_card.collected_data if response.workflow_card else None
                native = bool(tool_calls) and not any(call.get("fallback") for call in tool_calls)
                observation.update({"answer": response.short_answer, "workflow_status": response.workflow_status,
                                    "native_tool_selection": native, "collected_fields": fields,
                                    "decisions": decisions, "call_indices": list(range(call_start, len(recorder.calls)))})
                if name == "native_workflow":
                    observation["expected_answer"] = REPAIR_RECEIPT
                    observation["answer_is_authoritative"] = bool(
                        response.workflow_card
                        and response.short_answer == response.workflow_card.prompt == REPAIR_RECEIPT
                    )
                observation["passed"] = (
                    plain_chat_passed(response.short_answer, response.metadata or {}, recorder.calls[call_start:])
                    if name == "plain_chat" else
                    native and response.workflow_status == "completed" and bool(fields and fields.get("description"))
                    and any(item.get("accepted") and item.get("choice") == "repair" for item in decisions)
                    and observation["answer_is_authoritative"]
                )
            except Exception as exc:
                await db.rollback()
                observation.update({"passed": False, "error_type": type(exc).__name__})
            observation["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
            report["observations"].append(observation)
            print(json.dumps({"case": name, "passed": observation["passed"], "requests": recorder.counts}), flush=True)

        # Real decision API through the real executor, one call per populated
        # case. Missing input is deliberately local and must make no API call.
        for name, description, expected in ([] if runtime_only else CASES):
            session = ConversationSession(id=f"case-{name}", agent_id="support-agent", user_id="synthetic-user",
                                          tenant_id="verification", collected_data={} if description is None else {"description": description},
                                          workflow_state={"workflow_id": "support-flow", "current_step_index": 1, "status": "in_progress"})
            db.add(session)
            await db.commit()
            audit = AuditLogger(db, f"trace-{name}", session.id, session.agent_id, session.tenant_id)
            before = recorder.counts["jev"]
            start = time.perf_counter()
            observation = {"case": name, "kind": "workflow_executor", "input": description, "expected": expected}
            try:
                result = await WorkflowExecutor(db, ToolGateway(db, audit), audit).process_step(session, "")
                state = session.workflow_state or {}
                decision = state.get("decisions", {}).get("decision", {})
                actual_step = step_ids[state["current_step_index"]]
                expected_step = expected if expected in {"repair", "billing"} else "clarify"
                observation.update({"observed_step": actual_step, "status": result.status, "decision": decision,
                                    "provider_requests": recorder.counts["jev"] - before})
                observation["passed"] = actual_step == expected_step and (
                    decision.get("reason") == "missing_input" and recorder.counts["jev"] == before
                    if expected == "missing_input" else decision.get("choice") == expected
                )
                await db.commit()
                await audit.flush()
            except Exception as exc:
                await db.rollback()
                observation.update({"passed": False, "error_type": type(exc).__name__})
            observation["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
            report["observations"].append(observation)
            print(json.dumps({"case": name, "passed": observation["passed"], "requests": recorder.counts}), flush=True)
    report["request_counts"] = recorder.counts
    report["request_limits"] = LIMITS
    report["passed"] = sum(item.get("passed", False) for item in report["observations"])
    report["total"] = len(report["observations"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-only", action="store_true", help="Retest chat/native workflow without repeating labeled decisions.")
    args = parser.parse_args()
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    jev_key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("AEZAB_TYPESAFE_API_KEY", "")
    if not minimax_key or not jev_key:
        print("Set MINIMAX_API_KEY and TYPESAFE_API_KEY before running live verification.")
        return 2
    sys.path.insert(0, str(ROOT))
    logging.disable(logging.CRITICAL)
    report_name = "live-agent-workflows-runtime.json" if args.runtime_only else "live-agent-workflows.json"
    output = ROOT / "data" / "verification" / report_name
    recorder = RequestRecorder()
    report = {"passed": 0, "total": 0, "calls": recorder.calls, "request_counts": recorder.counts}

    async def observed_post(client, url, *args, **kwargs):
        return await recorder.post(client, url, *args, **kwargs)

    def save_report():
        serialized = json.dumps(report, ensure_ascii=False, indent=2)
        for secret in (minimax_key, jev_key):
            serialized = serialized.replace(secret, "[REDACTED]")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized + "\n", encoding="utf-8")

    async def run_and_close():
        try:
            return await verify(recorder, report, runtime_only=args.runtime_only)
        finally:
            from sqlalchemy.ext.asyncio import close_all_sessions
            from server.db import engine
            from server.engine.event_dispatcher import _background_tasks as event_tasks
            from server.engine.summary_scheduler import _background_tasks as summary_tasks
            # A completed workflow schedules its subscription lookup after
            # commit. Let those tasks release their DB sessions before shutdown.
            pending = list(event_tasks | summary_tasks)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await close_all_sessions()
            await engine.dispose()
            gc.collect()
            await asyncio.sleep(0)

    with tempfile.TemporaryDirectory(prefix="aezab-live-verification-", ignore_cleanup_errors=True) as directory:
        # Must happen before importing server modules, which bind their engine
        # at import time. This script runs in its own process.
        os.environ.update({
            "AEZAB_DATABASE_URL": "sqlite+aiosqlite:///" + (Path(directory) / "test.db").as_posix(),
            "AEZAB_LLM_BASE_URL": "https://api.minimaxi.com/v1", "AEZAB_LLM_API_KEY": minimax_key,
            "AEZAB_LLM_MODEL": "MiniMax-M2.7", "AEZAB_LLM_MAX_TOKENS": "2048",
            "AEZAB_LLM_TIMEOUT": "60", "AEZAB_TYPESAFE_ENABLED": "true",
            "AEZAB_TYPESAFE_API_KEY": jev_key, "AEZAB_DEBUG": "false",
        })
        try:
            with patch.object(httpx.AsyncClient, "post", observed_post):
                report = asyncio.run(run_and_close())
        except Exception as exc:
            report["fatal_error_type"] = type(exc).__name__
        # SQLite's cyclic driver references can retain Windows file handles
        # briefly even after the async pool has been disposed.
        gc.collect()
        # Persist before cleanup so Windows file-handle delays cannot destroy
        # observations from real billed requests.
        save_report()
    report["temporary_database_cleanup_complete"] = not Path(directory).exists()
    save_report()
    print(json.dumps({"report": str(output), "passed": report["passed"], "total": report["total"],
                      "request_counts": recorder.counts}, ensure_ascii=False))
    return 0 if report["total"] and report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
