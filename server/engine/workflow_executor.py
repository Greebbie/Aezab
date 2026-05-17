"""Workflow executor — drives a multi-step business process."""

from __future__ import annotations

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
        """Cancel the current workflow and preserve collected data for potential resume."""
        state = dict(session.workflow_state or {})
        state["status"] = "cancelled"
        session.workflow_state = state
        flag_modified(session, "workflow_state")
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

        if form_data:
            # Validate submitted data
            errors = []
            for field_def in fields:
                fname = field_def.get("name", "")
                ftype = field_def.get("field_type", "text")
                val = form_data.get(fname, "")

                # File field validation
                if ftype == "file":
                    err = self._validate_file_field(val, field_def)
                    if err:
                        errors.append(err)
                    else:
                        collected[fname] = val  # Store file reference (path/URL)
                    continue

                err = validate_field(str(val) if val else "", field_def)
                if err:
                    errors.append(err)
                else:
                    collected[fname] = val

            if errors:
                return WorkflowStepResult(
                    status="waiting_input",
                    message="请修正以下问题:\n" + "\n".join(f"- {e}" for e in errors),
                    card=self._make_card(step, steps, idx),
                )

            # LLM-assisted validation for fields that request it
            for field_def in fields:
                if not field_def.get("llm_validate"):
                    continue
                fname = field_def.get("name", "")
                val = collected.get(fname, "")
                if not val:
                    continue
                llm_err = await self._llm_validate_field(val, field_def)
                if llm_err:
                    errors.append(llm_err)

            if errors:
                return WorkflowStepResult(
                    status="waiting_input",
                    message="请修正以下问题:\n" + "\n".join(f"- {e}" for e in errors),
                    card=self._make_card(step, steps, idx),
                )

            # All valid → advance
            session.collected_data = collected
            flag_modified(session, "collected_data")
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

        try:
            result = await self.tool_gw.invoke(step.tool_id, tool_input)
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
        """Pause for human review."""
        if self.audit:
            self.audit.log("escalation", escalation_reason=f"步骤 '{step.name}' 需要人工审核",
                          workflow_meta={"step_id": step.id, "step_name": step.name})

        return WorkflowStepResult(
            status="escalated",
            message=step.prompt_template or "该步骤需要人工审核，请等待工作人员处理。",
            escalated=True,
        )

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
        if webhook_url and tool_config.get("webhook_enabled"):
            method = tool_config.get("webhook_method", "POST").upper()
            headers = tool_config.get("webhook_headers") or {}
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.request(method, webhook_url, json=user_data, headers=headers)
                    collected["_webhook_result"] = {
                        "status": resp.status_code,
                        "ok": resp.is_success,
                    }
                    session.collected_data = collected
                    flag_modified(session, "collected_data")
            except Exception as e:
                logger.warning("Webhook failed for workflow complete step '%s': %s", step.name, e)
                collected["_webhook_result"] = {
                    "status": 0,
                    "ok": False,
                    "error": str(e),
                }
                session.collected_data = collected
                flag_modified(session, "collected_data")
                if self.audit:
                    self.audit.log("workflow_step", workflow_meta={
                        "step_id": step.id,
                        "step_name": step.name,
                        "status": "webhook_failed",
                        "error": str(e),
                    })

        card = self._make_card(step, steps, idx, collected_data=user_data)

        # Attach webhook result to card for frontend visibility
        webhook_result = collected.get("_webhook_result")
        if webhook_result:
            card.webhook_result = webhook_result

        return WorkflowStepResult(
            status="completed",
            message=step.prompt_template or "流程已完成！感谢您的办理。",
            card=card,
        )

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
