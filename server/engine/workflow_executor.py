"""Workflow executor — drives a multi-step business process."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import httpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from server.models.workflow import WorkflowStep
from server.models.session import ConversationSession
from server.engine.tool_gateway import ToolGateway, ToolInvocationError
from server.engine.audit_logger import AuditLogger
from server.schemas.invoke import WorkflowCard

logger = logging.getLogger(__name__)

# Maximum recursion depth for auto-advancing non-interactive steps
MAX_AUTO_ADVANCE_DEPTH = 20

# Complete-step webhook retry policy (same exponential-backoff shape as
# ToolGateway.invoke: 3 retries beyond the first attempt -> 1s/2s/4s gaps).
WEBHOOK_MAX_RETRIES = 3
WEBHOOK_RETRY_BACKOFF_S = 1.0


# ── Built-in validators ─────────────────────────────────────────

BUILTIN_VALIDATORS = {
    "phone": re.compile(r"^1[3-9]\d{9}$"),
    "id_card": re.compile(r"^\d{17}[\dXx]$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "number": re.compile(r"^-?\d+(\.\d+)?$"),
}


def validate_field(value: str, field_def: dict) -> str | None:
    """Return error message or None if valid."""
    ftype = field_def.get("field_type", "text")
    required = field_def.get("required", True)

    if not value and required:
        return f"字段 '{field_def.get('label', '')}' 为必填项"
    if not value:
        return None

    # Built-in type check
    if ftype in BUILTIN_VALIDATORS:
        if not BUILTIN_VALIDATORS[ftype].match(value):
            return f"'{field_def.get('label', '')}' 格式不正确"

    # Custom regex
    rule = field_def.get("validation_rule")
    if rule:
        if not re.match(rule, value):
            return f"'{field_def.get('label', '')}' 不符合校验规则"

    return None


class WorkflowExecutor:
    """Execute a configured workflow step by step."""

    def __init__(self, db: AsyncSession, tool_gateway: ToolGateway, audit: AuditLogger | None = None):
        self.db = db
        self.tool_gw = tool_gateway
        self.audit = audit

    def cancel_workflow(self, session: ConversationSession) -> str:
        """Cancel the current workflow.

        Clears `collected_data` too (there is no cancelled-workflow resume
        feature) so a new workflow started afterward never inherits a
        previous ticket's field values — see W1-T5.
        """
        state = dict(session.workflow_state or {})
        state["status"] = "cancelled"
        session.workflow_state = state
        session.collected_data = {}
        flag_modified(session, "workflow_state")
        flag_modified(session, "collected_data")
        return "已退出当前流程。如需继续，随时告诉我。"

    async def get_steps(self, workflow_id: str) -> list[WorkflowStep]:
        result = await self.db.execute(
            select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id).order_by(WorkflowStep.order)
        )
        return list(result.scalars().all())

    async def process_step(
        self,
        session: ConversationSession,
        user_input: str,
        form_data: dict[str, Any] | None = None,
        _depth: int = 0,
    ) -> WorkflowStepResult:
        """Process the current step of the workflow for this session.

        Returns a WorkflowStepResult indicating what to show the user.
        """
        if _depth > MAX_AUTO_ADVANCE_DEPTH:
            return WorkflowStepResult(
                status="error",
                message=f"流程自动推进超过最大深度 ({MAX_AUTO_ADVANCE_DEPTH})，已停止执行。请检查流程配置。",
            )

        state = session.workflow_state or {}
        workflow_id = state.get("workflow_id")
        current_step_index = state.get("current_step_index", 0)

        if not workflow_id:
            return WorkflowStepResult(status="error", message="会话未关联工作流")

        steps = await self.get_steps(workflow_id)
        if not steps:
            return WorkflowStepResult(status="error", message="工作流无步骤配置")

        if current_step_index >= len(steps):
            return WorkflowStepResult(status="completed", message="流程已完成")

        step = steps[current_step_index]
        collected = dict(session.collected_data or {})

        # Save step snapshot for potential rollback
        state_snap = session.workflow_state or {}
        snapshots = list(state_snap.get("snapshots", []))
        snapshots.append({
            "step_index": current_step_index,
            "collected_data": dict(collected),
        })
        # Keep only last 5 snapshots to avoid unbounded growth
        if len(snapshots) > 5:
            snapshots = snapshots[-5:]
        state_snap["snapshots"] = snapshots
        session.workflow_state = state_snap
        flag_modified(session, "workflow_state")

        # ── Handle step by type ──────────────────────────────────
        if step.step_type == "collect":
            return await self._handle_collect(step, steps, current_step_index, user_input, form_data, collected, session, _depth)
        elif step.step_type == "validate":
            return await self._handle_validate(step, steps, current_step_index, collected, session, _depth)
        elif step.step_type == "tool_call":
            return await self._handle_tool_call(step, steps, current_step_index, collected, session, _depth)
        elif step.step_type == "confirm":
            return await self._handle_confirm(step, steps, current_step_index, user_input, collected, session, _depth)
        elif step.step_type == "human_review":
            return await self._handle_human_review(step, steps, current_step_index, session)
        elif step.step_type == "complete":
            return await self._handle_complete(step, steps, current_step_index, collected, session)
        else:
            return WorkflowStepResult(status="error", message=f"未知步骤类型: {step.step_type}")

    async def _handle_collect(
        self, step, steps, idx, user_input, form_data, collected, session, _depth: int = 0,
    ) -> WorkflowStepResult:
        """Collect form fields from user, including file uploads and LLM validation."""
        fields = step.fields or []

        # No fields to collect — this is a display-only step, auto-advance
        if not fields:
            return await self._advance(steps, idx, session, _depth)

        # ── Natural-language field extraction ─────────────────────
        # form_data (structured submission) always wins over free-form chat.
        # Only attempt extraction when there is no form_data but the user did
        # say something.
        effective_form_data = form_data
        if not effective_form_data and (user_input or "").strip():
            extracted = await self._extract_fields_from_text(user_input, fields, collected)
            if extracted is None:
                # LLM unavailable or extraction failed outright — never crash
                # the workflow, fall back to today's re-prompt behavior.
                effective_form_data = None
            elif not extracted:
                # Valid (parseable) but empty extraction — the message was
                # off-topic / didn't contain any recognizable field values.
                return WorkflowStepResult(
                    status="waiting_input",
                    message=(
                        (step.prompt_template or f"请填写以下信息: {', '.join(f.get('label', '') for f in fields)}")
                        + '\n（如需退出当前流程，请回复"取消"）'
                    ),
                    card=self._make_card(step, steps, idx),
                )
            else:
                effective_form_data = extracted

        if effective_form_data:
            # Incremental validation: fields may arrive across several turns
            # (one message per field in conversational mode). Fields already
            # collected in earlier turns are not re-required; valid values
            # from this turn are persisted even when other fields are still
            # missing, so partial progress is never thrown away.
            errors = []
            missing_labels = []
            provided_fields = []
            for field_def in fields:
                fname = field_def.get("name", "")
                ftype = field_def.get("field_type", "text")

                if fname not in effective_form_data:
                    if not collected.get(fname) and field_def.get("required", True):
                        missing_labels.append(field_def.get("label") or fname)
                    continue

                val = effective_form_data.get(fname, "")
                if ftype == "file":
                    err = self._validate_file_field(val, field_def)
                else:
                    err = validate_field(str(val) if val else "", field_def)
                if err:
                    errors.append(err)
                else:
                    collected[fname] = val
                    provided_fields.append(field_def)

            # LLM-assisted validation only for fields provided this turn
            for field_def in provided_fields:
                if not field_def.get("llm_validate"):
                    continue
                fname = field_def.get("name", "")
                val = collected.get(fname, "")
                if not val:
                    continue
                llm_err = await self._llm_validate_field(val, field_def)
                if llm_err:
                    errors.append(llm_err)
                    collected.pop(fname, None)
                    if field_def.get("required", True):
                        missing_labels.append(field_def.get("label") or fname)

            # Persist whatever validated cleanly, even on a partial turn
            if provided_fields:
                session.collected_data = collected
                flag_modified(session, "collected_data")

            if errors:
                return WorkflowStepResult(
                    status="waiting_input",
                    message="请修正以下问题:\n" + "\n".join(f"- {e}" for e in errors),
                    card=self._make_card(step, steps, idx),
                )

            if missing_labels:
                saved = [
                    (f.get("label") or f.get("name", ""))
                    for f in fields if collected.get(f.get("name", ""))
                ]
                saved_note = f"已记录：{'、'.join(saved)}。\n" if saved else ""
                return WorkflowStepResult(
                    status="waiting_input",
                    message=f"{saved_note}还需要以下信息：{'、'.join(missing_labels)}",
                    card=self._make_card(step, steps, idx),
                )

            # All fields present and valid → advance
            return await self._advance(steps, idx, session, _depth)

        # No form data yet — prompt user
        return WorkflowStepResult(
            status="waiting_input",
            message=step.prompt_template or f"请填写以下信息: {', '.join(f.get('label', '') for f in fields)}",
            card=self._make_card(step, steps, idx),
        )

    @staticmethod
    def _validate_file_field(value: Any, field_def: dict) -> str | None:
        """Validate a file field value against file_config constraints."""
        required = field_def.get("required", True)
        if not value and required:
            return f"字段 '{field_def.get('label', '')}' 为必填项"
        if not value:
            return None

        file_config = field_def.get("file_config") or {}
        allowed_ext = file_config.get("allowed_extensions")
        max_size_mb = file_config.get("max_size_mb")

        # If value is a filename/path, check extension
        if isinstance(value, str) and allowed_ext:
            ext = os.path.splitext(value)[1].lower()
            allowed = [e.lower() if e.startswith(".") else f".{e.lower()}" for e in allowed_ext]
            if ext not in allowed:
                return f"'{field_def.get('label', '')}' 不支持此文件格式，仅支持: {', '.join(allowed_ext)}"

        # Size check is handled at API/upload level, not here
        if max_size_mb and isinstance(value, dict) and value.get("size"):
            size_mb = value["size"] / (1024 * 1024)
            if size_mb > max_size_mb:
                return f"'{field_def.get('label', '')}' 文件过大，最大 {max_size_mb}MB"

        return None

    async def _extract_fields_from_text(
        self, user_input: str, fields: list[dict], collected: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Ask the LLM to extract field values from a free-form chat message.

        Returns:
        - a dict (possibly empty) when the LLM call succeeded and its
          response could be parsed as a JSON object — an empty dict means
          the message did not contain any recognizable field value.
        - None when the LLM is unavailable, the call failed, or its
          response could not be parsed at all. Callers must treat None as
          "extraction did not happen" and fall back to the legacy prompt —
          never crash the workflow because of an LLM hiccup.
        """
        try:
            from server.engine.llm_adapter import LLMMessage, get_llm_adapter
            llm = get_llm_adapter()
        except Exception as e:
            logger.warning("LLM unavailable for field extraction: %s", e)
            return None

        field_lines = []
        for f in fields:
            requiredness = "必填" if f.get("required", True) else "选填"
            field_lines.append(
                f"- 字段名: {f.get('name', '')}，标签: {f.get('label', '')}，"
                f"类型: {f.get('field_type', 'text')}，{requiredness}"
            )

        already_collected = {k: v for k, v in (collected or {}).items() if not str(k).startswith("_")}

        prompt = f"""请从用户的输入中提取以下字段的值，只输出一个JSON对象。

字段定义:
{chr(10).join(field_lines)}

已收集的信息（无需重复提取）: {json.dumps(already_collected, ensure_ascii=False)}

用户输入: {user_input}

规则:
1. 只输出JSON对象，key为字段名，value为从用户输入中提取到的值。
2. 只提取用户在本次输入中明确提供的字段，绝不猜测或编造用户未提供的字段。
3. 如果用户的输入没有提供任何可识别的字段值，输出空JSON对象 {{}}。
4. 不要输出JSON对象以外的任何说明文字。"""

        try:
            resp = await llm.chat(
                [LLMMessage(role="user", content=prompt)],
                max_tokens=300,
                temperature=0.0,
            )
        except Exception as e:
            logger.warning("Field extraction LLM call failed: %s", e)
            if self.audit:
                self.audit.log("workflow_step", workflow_meta={
                    "status": "field_extraction_failed",
                    "error": str(e),
                })
            return None

        extracted = self._parse_extraction_json(resp.content)
        if extracted is None:
            logger.warning("Field extraction returned unparseable content: %r", resp.content)
            if self.audit:
                self.audit.log("workflow_step", workflow_meta={
                    "status": "field_extraction_unparseable",
                    "raw": (resp.content or "")[:200],
                })
        return extracted

    @staticmethod
    def _parse_extraction_json(content: str | None) -> dict[str, Any] | None:
        """Defensively parse the LLM's extraction response into a dict.

        Handles bare JSON, ```json fenced blocks, and JSON embedded in
        surrounding prose. Returns None if nothing parseable is found.
        """
        text = (content or "").strip()
        if not text:
            return None

        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidate = fence_match.group(1) if fence_match else text

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            brace_match = re.search(r"\{.*\}", text, re.DOTALL)
            if not brace_match:
                return None
            try:
                data = json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                return None

        if not isinstance(data, dict):
            return None
        return data

    async def _llm_validate_field(self, value: str, field_def: dict) -> str | None:
        """Use LLM to semantically validate a field value."""
        prompt = field_def.get("llm_validate_prompt")
        if not prompt:
            return None

        try:
            from server.engine.llm_adapter import LLMMessage, get_llm_adapter
            llm = get_llm_adapter()
            validation_prompt = f"""{prompt}

用户输入: {value}

如果输入有效，只回复"OK"。如果无效，回复错误原因(一句话)。"""

            resp = await llm.chat(
                [LLMMessage(role="user", content=validation_prompt)],
                max_tokens=100,
                temperature=0.0,
            )
            result = resp.content.strip()
            if result.upper() in ("OK", "有效", "正确", "通过"):
                return None
            return f"'{field_def.get('label', '')}': {result}"
        except Exception as e:
            logger.warning(f"LLM validation failed for field '{field_def.get('name')}': {e}")
            if self.audit:
                self.audit.log("workflow_step", workflow_meta={
                    "status": "llm_validation_skipped",
                    "field": field_def.get("name"),
                    "error": str(e),
                })
            return None  # Fail open: if LLM validation fails, don't block the user

    async def _handle_validate(self, step, steps, idx, collected, session, _depth: int = 0) -> WorkflowStepResult:
        """Run validation rules on collected data."""
        rules = step.validation_rules or {}
        errors = []
        for field_name, rule in rules.items():
            val = collected.get(field_name, "")
            if rule.get("required") and not val:
                errors.append(f"缺少必填字段: {field_name}")
            if rule.get("regex") and val:
                if not re.match(rule["regex"], str(val)):
                    errors.append(f"字段 {field_name} 格式不正确")

        if errors:
            if step.on_failure == "rollback":
                return self._perform_rollback(session, steps, current_step_index=idx)
            return WorkflowStepResult(
                status="waiting_input",
                message="校验失败:\n" + "\n".join(f"- {e}" for e in errors),
            )

        return await self._advance(steps, idx, session, _depth)

    async def _handle_tool_call(self, step, steps, idx, collected, session, _depth: int = 0) -> WorkflowStepResult:
        """Invoke a bound tool."""
        if not step.tool_id:
            return await self._advance(steps, idx, session, _depth)

        # Map collected data to tool input
        tool_config = step.tool_config or {}
        input_mapping = tool_config.get("input_mapping", {})
        tool_input = {}
        for tool_field, source_value in input_mapping.items():
            # If source_value matches a collected data key, use that; otherwise treat as literal value
            tool_input[tool_field] = collected.get(source_value, source_value)

        # If no explicit mapping, pass all collected data
        if not input_mapping:
            tool_input = collected

        # Submission idempotency: reuse the workflow-level key so a retried
        # tool call can be deduplicated by the receiving end.
        idempotency_key = (session.workflow_state or {}).get("idempotency_key")
        extra_headers = None
        if idempotency_key:
            idem_value = f"{idempotency_key}:{step.id}"
            extra_headers = {"X-Idempotency-Key": idem_value}
            tool_input = {**tool_input, "idempotency_key": idem_value}

        try:
            result = await self.tool_gw.invoke(step.tool_id, tool_input, extra_headers=extra_headers)
            # Store tool output
            output_mapping = tool_config.get("output_mapping", {})
            for local_field, tool_field in output_mapping.items():
                collected[local_field] = result.get(tool_field, "")
            collected[f"_tool_result_{step.name}"] = result
            session.collected_data = collected
            flag_modified(session, "collected_data")

            if self.audit:
                self.audit.log("workflow_step", workflow_meta={
                    "step_id": step.id, "step_name": step.name,
                    "status": "tool_success", "tool_id": step.tool_id,
                })

            return await self._advance(steps, idx, session, _depth)

        except ToolInvocationError as e:
            if self.audit:
                self.audit.log("workflow_step", workflow_meta={
                    "step_id": step.id, "step_name": step.name,
                    "status": "tool_failed", "error": str(e),
                })

            if step.on_failure == "skip":
                return await self._advance(steps, idx, session, _depth)
            elif step.on_failure == "escalate":
                # Tool-call escalation is a hard stop this wave (no resume
                # path like human_review's "paused_for_review"), so clear
                # the workflow ourselves rather than relying on the caller
                # to treat "escalated" as terminal.
                session.workflow_state = None
                session.collected_data = {}
                session.active_skill_id = None
                flag_modified(session, "workflow_state")
                flag_modified(session, "collected_data")
                return WorkflowStepResult(
                    status="escalated",
                    message=f"工具调用失败，已转人工处理。原因: {e}",
                    escalated=True,
                )
            elif step.on_failure == "rollback":
                return self._perform_rollback(session, steps, current_step_index=idx)
            else:
                return WorkflowStepResult(
                    status="error",
                    message=f"操作失败: {e}。请稍后重试。",
                    card=self._make_card(step, steps, idx),
                )

    async def _handle_confirm(self, step, steps, idx, user_input, collected, session, _depth: int = 0) -> WorkflowStepResult:
        """Ask user to confirm before proceeding."""
        text = user_input.strip().lower()

        # Check for negative/cancel keywords first. Single-letter choices must
        # be exact matches; otherwise "confirm" contains "n" and rolls back.
        negative_exact = {"不", "n", "no"}
        negative_contains = {"取消", "不是", "不对", "不行", "重新", "修改", "cancel"}
        if text in negative_exact or any(kw in text for kw in negative_contains):
            # Roll back to previous collect step
            return self._perform_rollback(session, steps, current_step_index=idx)

        # Check for positive/confirm keywords.
        positive_exact = {"y", "yes", "ok", "sure"}
        positive_contains = {"确认", "确定", "是", "好", "对", "行", "可以", "没问题", "正确", "confirm"}
        if text in positive_exact or any(kw in text for kw in positive_contains):
            return await self._advance(steps, idx, session, _depth)

        # No clear intent — re-prompt
        return WorkflowStepResult(
            status="waiting_input",
            message=step.prompt_template or '请确认以上信息是否正确？（回复"确认"继续，或"取消"修改）',
            card=self._make_card(step, steps, idx),
        )

    async def _handle_human_review(self, step, steps, idx, session) -> WorkflowStepResult:
        """Pause for human review — resumable, unlike a hard escalation.

        Keeps `workflow_state` alive with status "paused_for_review" (and
        the current step index) instead of letting the caller treat
        "escalated" as terminal and wipe it. A later "继续"/"resume" message
        advances past this step (see AgentRuntime._continue_active_workflow).
        """
        if self.audit:
            self.audit.log("escalation", escalation_reason=f"步骤 '{step.name}' 需要人工审核",
                          workflow_meta={"step_id": step.id, "step_name": step.name})

        session.workflow_state = {
            **(session.workflow_state or {}),
            "current_step_index": idx,
            "status": "paused_for_review",
        }
        flag_modified(session, "workflow_state")

        return WorkflowStepResult(
            status="escalated",
            message=step.prompt_template or "该步骤需要人工审核，请等待工作人员处理。",
            escalated=True,
        )

    async def resume_after_review(self, session: ConversationSession) -> WorkflowStepResult:
        """Advance past a human_review step after the user asks to resume.

        MVP resume (no external human-approval API this wave): the user's
        own "继续"/"resume" message is treated as the reviewer having
        finished, and we simply move on to the next step.
        """
        state = session.workflow_state or {}
        workflow_id = state.get("workflow_id")
        current_step_index = state.get("current_step_index", 0)
        if not workflow_id:
            return WorkflowStepResult(status="error", message="会话未关联工作流")

        steps = await self.get_steps(workflow_id)
        if not steps or current_step_index >= len(steps):
            return WorkflowStepResult(status="error", message="无法恢复：工作流状态无效。")

        return await self._advance(steps, current_step_index, session)

    async def _handle_complete(self, step, steps, idx, collected, session) -> WorkflowStepResult:
        """Final step — workflow is done.  Optionally POST collected data to a webhook."""
        session.workflow_state = {
            **(session.workflow_state or {}),
            "current_step_index": idx,
            "status": "completed",
        }
        flag_modified(session, "workflow_state")
        # Filter out internal keys (e.g. _tool_result_*) for the user-facing summary
        user_data = {k: v for k, v in collected.items() if not k.startswith("_")}

        # ── Webhook: send collected data to external endpoint ──
        tool_config = step.tool_config or {}
        webhook_url = tool_config.get("webhook_url")
        webhook_exhausted = False
        if webhook_url and tool_config.get("webhook_enabled"):
            webhook_exhausted = not await self._deliver_complete_webhook(
                step, idx, tool_config, webhook_url, user_data, collected, session,
            )

        card = self._make_card(step, steps, idx, collected_data=user_data)

        # Attach webhook result to card for frontend visibility
        webhook_result = collected.get("_webhook_result")
        if webhook_result:
            card.webhook_result = webhook_result

        if webhook_exhausted:
            # Keep the workflow alive on this same (complete) step so the
            # user's next message can retry instead of hitting a dead end.
            return WorkflowStepResult(
                status="await_retry",
                message=(
                    "已收集完所有信息，但提交到外部系统失败（已多次重试），数据已保留。"
                    '回复"重试"重新提交，或"取消"放弃。'
                ),
                card=card,
            )

        # Honest completion message: never claim success if the webhook failed
        base_msg = step.prompt_template or "流程已完成！感谢您的办理。"

        return WorkflowStepResult(
            status="completed",
            message=base_msg,
            card=card,
        )

    async def _deliver_complete_webhook(
        self, step, idx, tool_config, webhook_url, user_data, collected, session,
    ) -> bool:
        """POST collected data to the complete-step webhook with retry.

        Returns True on success, False once all retries are exhausted. On
        exhaustion, sets `session.workflow_state["status"] = "await_retry"`
        so the caller does not treat the workflow as finished.
        """
        method = tool_config.get("webhook_method", "POST").upper()
        headers = dict(tool_config.get("webhook_headers") or {})

        # Submission idempotency: reuse the workflow-level key so a retried
        # webhook delivery can be deduplicated by the receiver.
        idempotency_key = (session.workflow_state or {}).get("idempotency_key")
        idem_value = f"{idempotency_key}:{step.id}" if idempotency_key else None
        if idem_value:
            headers["X-Idempotency-Key"] = idem_value
        webhook_body = {**user_data, "idempotency_key": idem_value} if idem_value else dict(user_data)

        last_error: Exception | None = None
        last_status: int | None = None

        for attempt in range(WEBHOOK_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.request(method, webhook_url, json=webhook_body, headers=headers)
                if resp.is_success:
                    collected["_webhook_result"] = {"status": resp.status_code, "ok": True}
                    session.collected_data = collected
                    flag_modified(session, "collected_data")
                    return True
                last_status = resp.status_code
                last_error = None
            except Exception as e:
                last_error = e
                last_status = None

            if attempt < WEBHOOK_MAX_RETRIES:
                await asyncio.sleep(WEBHOOK_RETRY_BACKOFF_S * (2 ** attempt))

        # All attempts exhausted
        total_attempts = WEBHOOK_MAX_RETRIES + 1
        if last_error is not None:
            logger.warning(
                "Webhook failed for workflow complete step '%s' after %d attempts: %s",
                step.name, total_attempts, last_error,
            )
        collected["_webhook_result"] = {
            "status": last_status or 0,
            "ok": False,
            "error": str(last_error) if last_error else None,
            "attempts": total_attempts,
        }
        session.collected_data = collected
        flag_modified(session, "collected_data")
        session.workflow_state = {
            **(session.workflow_state or {}),
            "current_step_index": idx,
            "status": "await_retry",
            "webhook_ok": False,
        }
        flag_modified(session, "workflow_state")
        if self.audit:
            self.audit.log("workflow_step", workflow_meta={
                "step_id": step.id,
                "step_name": step.name,
                "status": "webhook_failed",
                "error": str(last_error) if last_error else None,
                "http_status": last_status,
                "attempts": total_attempts,
            })
        return False

    async def _advance(self, steps, current_idx, session, _depth: int = 0) -> WorkflowStepResult:
        """Move to the next step, evaluating conditional branching rules if present."""
        current_step = steps[current_idx]
        next_idx = current_idx + 1  # default: linear progression

        # Evaluate conditional branching rules
        if current_step.next_step_rules:
            from server.engine.rule_evaluator import evaluate_rules
            collected = dict(session.collected_data or {})
            rules = current_step.next_step_rules
            # Handle both list and dict-wrapped formats
            if isinstance(rules, dict):
                rules = rules.get("rules", [])
            if isinstance(rules, list):
                goto = evaluate_rules(rules, collected)
                if goto is not None:
                    resolved = self._resolve_step_target(steps, goto)
                    if resolved is not None:
                        next_idx = resolved
                        logger.info(
                            "Workflow branching: step '%s' -> '%s' (index %d)",
                            current_step.name, goto, next_idx,
                        )

        if next_idx >= len(steps):
            session.workflow_state = {
                **(session.workflow_state or {}),
                "current_step_index": next_idx,
                "status": "completed",
            }
            flag_modified(session, "workflow_state")
            # Build completion message with tool results
            collected = dict(session.collected_data or {})
            user_data = {k: v for k, v in collected.items() if not k.startswith("_")}
            tool_results = [v for k, v in collected.items() if k.startswith("_tool_result_") and isinstance(v, dict)]
            completion_card = WorkflowCard(
                step_name="完成",
                step_type="complete",
                prompt="流程已全部完成！",
                current_step=len(steps),
                total_steps=len(steps),
                collected_data=user_data,
            )
            if tool_results:
                last = tool_results[-1]
                # Use formatted/forecast/result fields if available, otherwise join key=value
                for display_key in ("formatted", "forecast", "result", "datetime"):
                    if display_key in last and last[display_key]:
                        return WorkflowStepResult(status="completed", message=str(last[display_key]), card=completion_card)
                summary_parts = [f"{k}: {v}" for k, v in last.items() if k != "success" and v]
                msg = "\n".join(summary_parts) if summary_parts else "流程已全部完成！"
                return WorkflowStepResult(status="completed", message=msg, card=completion_card)
            return WorkflowStepResult(status="completed", message="流程已全部完成！", card=completion_card)

        # Not yet complete — advance step index
        session.workflow_state = {
            **(session.workflow_state or {}),
            "current_step_index": next_idx,
        }
        flag_modified(session, "workflow_state")

        next_step = steps[next_idx]

        # Auto-execute non-interactive steps (with depth guard)
        # Also auto-execute collect steps with no fields (display-only steps)
        is_auto = next_step.step_type in ("validate", "tool_call", "complete", "human_review")
        if not is_auto and next_step.step_type == "collect" and not (next_step.fields or []):
            is_auto = True
        if is_auto:
            return await self.process_step(session, "", None, _depth=_depth + 1)

        return WorkflowStepResult(
            status="in_progress",
            message=next_step.prompt_template or f"请继续: {next_step.name}",
            card=self._make_card(next_step, steps, next_idx),
        )

    @staticmethod
    def _resolve_step_target(steps: list, target: str) -> int | None:
        """Resolve a goto_step target to a step index.

        Target can be:
        - A step name (string match)
        - A step order number (as string or int)
        """
        # Try matching by step name first
        for i, step in enumerate(steps):
            if step.name == target:
                return i

        # Try matching by order number
        try:
            target_order = int(target)
            for i, step in enumerate(steps):
                if step.order == target_order:
                    return i
        except (ValueError, TypeError):
            pass

        logger.warning("Could not resolve step target '%s'", target)
        return None

    def _perform_rollback(
        self, session: ConversationSession, steps: list, current_step_index: int,
    ) -> WorkflowStepResult:
        """Roll back to the previous interactive step by restoring its snapshot."""
        state = dict(session.workflow_state or {})
        snapshots = list(state.get("snapshots", []))

        if not snapshots:
            return WorkflowStepResult(
                status="error",
                message="无法回退：没有可用的步骤快照。",
            )

        # Pop the last snapshot (which is the current step's snapshot)
        snapshots.pop()

        if not snapshots:
            # No previous snapshot — go back to step 0
            target_idx = 0
            restored_data: dict[str, Any] = {}
        else:
            # Restore the previous snapshot
            prev = snapshots[-1]
            target_idx = prev["step_index"]
            restored_data = dict(prev.get("collected_data", {}))

        # Update session state
        state["current_step_index"] = target_idx
        state["snapshots"] = snapshots
        session.workflow_state = state
        session.collected_data = restored_data
        flag_modified(session, "workflow_state")
        flag_modified(session, "collected_data")

        target_step = steps[target_idx] if target_idx < len(steps) else steps[0]
        logger.info(
            "Workflow rollback: step %d -> step %d ('%s')",
            current_step_index, target_idx, target_step.name,
        )

        return WorkflowStepResult(
            status="waiting_input",
            message=f"已回退到步骤: {target_step.name}。请重新填写。",
            card=self._make_card(target_step, steps, target_idx),
        )

    def _make_card(
        self, step: WorkflowStep, steps: list[WorkflowStep], idx: int,
        collected_data: dict[str, Any] | None = None,
    ) -> WorkflowCard:
        return WorkflowCard(
            step_name=step.name,
            step_type=step.step_type,
            prompt=step.prompt_template or "",
            fields=step.fields,
            current_step=idx + 1,
            total_steps=len(steps),
            collected_data=collected_data,
        )


class WorkflowStepResult:
    """Result of processing one workflow step."""

    def __init__(
        self,
        status: str,
        message: str,
        card: WorkflowCard | None = None,
        escalated: bool = False,
    ):
        self.status = status  # waiting_input | in_progress | completed | escalated | error | rollback
        self.message = message
        self.card = card
        self.escalated = escalated
