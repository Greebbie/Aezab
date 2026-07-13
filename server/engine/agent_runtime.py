"""Agent Runtime — the core orchestrator.

Pipeline: Input → Risk Check → Conversational LLM (with function calling) → Response

Architecture: Conversation-first — LLM drives the conversation naturally,
skills are exposed as function-calling tools. No explicit router/classifier layer.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from server.models.agent import Agent
from server.models.agent_skill import AgentSkill
from server.models.session import ConversationSession, Message
from server.models.skill import Skill
from server.models.tool import ToolDefinition
from server.models.workflow import Workflow
from server.schemas.invoke import (
    InvokeRequest,
    InvokeResponse,
    Citation,
    WorkflowCard,
)
from server.engine.llm_adapter import LLMMessage, LLMStreamError, get_llm_adapter_for_agent
from server.engine.context_budget import (
    MAX_INPUT_TOKENS, MAX_TOOL_RESULT_TOKENS, trim_messages, truncate_text,
)
from server.engine.knowledge_retriever import KnowledgeRetriever
from server.engine.tool_gateway import ToolGateway
from server.engine.workflow_executor import WorkflowExecutor
from server.engine.audit_logger import AuditLogger, new_trace_id
from server.engine.event_dispatcher import emit_event
from server.engine.summary_scheduler import (
    RECENT_WINDOW,
    SUMMARY_THRESHOLD,
    schedule_summary_update,
)
from server.engine.vector_store import get_vector_store_if_initialized
from server.runtime_config import runtime_config

logger = logging.getLogger(__name__)

# Maximum tool-calling rounds before forcing a final answer
MAX_TOOL_ROUNDS = 5

# Maximum delegation depth
MAX_DELEGATION_DEPTH = 3

# ── Current-session memory (Wave 4 / Workstream M) ─────────────────
# SUMMARY_THRESHOLD / RECENT_WINDOW now live in server.engine.summary_scheduler
# (Wave 5 / Workstream B — the fold itself moved off the critical path) and
# are re-exported here so existing call sites/tests keep working. See that
# module's docstring for the full rationale.

# Event callback type threaded through the conversational pipeline for
# real-time progress reporting (SSE, webhooks, etc.). Emission is always
# fire-and-forget: a raising callback must never break the pipeline.
EventCallback = Callable[[str, dict], Awaitable[None]]


async def _emit_event(event_cb: EventCallback | None, event_type: str, data: dict) -> None:
    """Fire an event_cb callback, swallowing (and logging) any failure."""
    if event_cb is None:
        return
    try:
        await event_cb(event_type, data)
    except Exception as e:
        logger.warning(f"event_cb raised for event '{event_type}': {e}")


# ── SkillToolResult ─────────────────────────────────────────────

@dataclass
class SkillToolResult:
    """Result returned by a skill-tool handler to the conversational loop."""
    text: str
    citations: list[Citation] | None = None
    workflow_card: WorkflowCard | None = None
    workflow_status: str | None = None
    skill_info: dict | None = None


@dataclass
class ActionIntent:
    """A strong signal that the user is asking the agent to do work."""
    kind: str | None = None
    matched: str | None = None

    @property
    def detected(self) -> bool:
        return self.kind is not None


# ── Prompt templates ─────────────────────────────────────────────

CONVERSATIONAL_SYSTEM_PROMPT = """{persona}
规则：用户要办理业务、调用工具、计算、查询外部系统或启动流程时，必须调用可用工具，不要只用文字回答；用户询问信息时，简洁回答 1-3 句。"""

CONVERSATIONAL_SYSTEM_PROMPT_WITH_CONTEXT = """{persona}

参考资料：
{context}

规则：如果用户只是询问信息，请基于参考资料简洁回答 1-3 句，并保留事实准确性；如果用户要办理业务、调用工具、计算或启动流程，必须调用可用工具，不要被参考资料分散。资料不足时再调用知识搜索工具。禁止说“没有提供”“未找到”。"""


# ── Refusal phrases ─────────────────────────────────────────────

_REFUSAL_PHRASES = [
    "暂无", "无此", "没有相关", "无法找到", "不确定", "未找到",
    "没有提供", "没有包含", "不包含", "未提及", "未提供",
    "没有找到", "未能找到", "无法确定", "没有数据", "无数据",
    "没有记录", "未收录", "资料中没有", "参考资料不足",
]


def _apply_refusal_supplement(
    answer: str, citations: list[Citation],
) -> tuple[str, bool]:
    """If the answer is a refusal but retrieval found data, append the top
    snippet as a clearly-labeled reference instead of silently replacing
    the answer. Returns (answer, was_supplemented)."""
    if not citations or not any(p in answer for p in _REFUSAL_PHRASES):
        return answer, False
    snippet = next((c.content_snippet for c in citations if c.content_snippet), None)
    if not snippet:
        return answer, False
    supplemented = (
        f"{answer}\n\n---\n以下是检索到的可能相关的资料片段，供参考：\n{snippet}"
    )
    return supplemented, True


# ── Workflow exit keywords ─────────────────────────────────────

_WORKFLOW_EXIT_KEYWORDS = {
    "取消", "退出", "不办了", "算了", "不要了", "放弃",
    "cancel", "quit", "exit", "abort", "stop",
}

_WORKFLOW_EXIT_INTENT_KEYWORDS = {
    "取消", "退出", "结束", "停止", "中止", "终止", "不办了", "不办理了",
    "算了", "算了吧", "不要了", "不用了", "不弄了", "不修了", "不报修了",
    "先不办", "先不弄", "先不修", "暂时不办", "暂时不用", "暂时不处理",
    "不用处理", "不用继续", "不用报修", "不用提交", "放弃", "停一下",
    "停掉", "别报修", "别提交", "不想继续", "不想填", "不想提交",
    "不想创建", "回到聊天", "回普通聊天", "换个问题", "先这样",
    "cancel", "quit", "exit", "abort", "stop",
    "never mind", "nvm", "forget it", "no need", "not needed",
    "do not continue", "don't continue", "stop workflow", "end workflow",
    "back to chat", "cancel workflow",
}

_WORKFLOW_RETRY_KEYWORDS = {"重试", "重新提交", "再试一次", "retry"}

_WORKFLOW_RESUME_KEYWORDS = {"继续", "resume"}

_WORKFLOW_EXIT_NEGATION_PHRASES = {
    "不要取消", "别取消", "不取消", "不是取消", "不要退出", "别退出",
    "不退出", "不要停止", "别停止",
    "do not cancel", "don't cancel", "not cancel",
}


def _is_workflow_exit_intent(message: str) -> bool:
    """Return True when an active workflow should be cancelled.

    Active workflow turns intentionally bypass the LLM so form submission stays
    deterministic. This local detector covers common natural cancellation
    phrasing without spending a model call.
    """
    text = (message or "").strip().lower()
    if not text:
        return False

    if any(phrase in text for phrase in _WORKFLOW_EXIT_NEGATION_PHRASES):
        return False

    if any(keyword in text for keyword in _WORKFLOW_EXIT_KEYWORDS):
        return True

    if any(keyword in text for keyword in _WORKFLOW_EXIT_INTENT_KEYWORDS):
        return True

    rejection_markers = (
        "不想", "不需要", "不用", "不要", "先不", "暂时不", "先别",
        "算了", "等会", "晚点", "later", "not now",
    )
    workflow_objects = (
        "流程", "工单", "报修", "维修", "申请", "办理", "提交", "填写",
        "workflow", "ticket", "work order", "repair", "form", "submit",
    )
    return any(marker in text for marker in rejection_markers) and any(
        obj in text for obj in workflow_objects
    )


_FIELD_VALUE_PATTERN = re.compile(
    r"^[\w@.+\-]+$|^\d[\d\-/: ]*\d$|^[一-鿿]{1,12}$"
)


def _looks_like_plain_field_value(message: str) -> bool:
    """Heuristic: does this message look like a raw field value (phone
    number, email, date, a short name) rather than a full sentence that
    could plausibly (but ambiguously) express workflow intent?

    Used only to decide whether spending an LLM call on exit-intent
    classification is worthwhile — never used for validation itself.
    """
    text = (message or "").strip()
    if not text:
        return False
    # Anything containing whitespace or typical sentence punctuation reads
    # as free-form natural language, not a single field value. (ASCII '.'
    # is deliberately excluded from this list — it's common in emails and
    # decimals, not just sentence-ending punctuation.)
    if any(ch in text for ch in " \t，。？！,?!~；;"):
        return False
    return bool(_FIELD_VALUE_PATTERN.match(text))


def _parse_workflow_exit_decision(content: str) -> bool | None:
    """Parse the LLM's active-workflow intent classification."""
    text = (content or "").strip()
    if not text:
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            lowered = text.lower()
            if "cancel_workflow" in lowered or lowered == "cancel":
                return True
            if "continue_workflow" in lowered or lowered == "continue":
                return False
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    action = str(data.get("action") or data.get("intent") or "").strip().lower()
    if action in {"cancel_workflow", "cancel", "exit", "stop"}:
        return True
    if action in {"continue_workflow", "continue", "proceed", "edit", "answer_question"}:
        return False
    return None


_WORKFLOW_ACTION_KEYWORDS = [
    "报修", "维修", "修一下", "修理", "我要办", "帮我办",
    "我要申请", "我想申请", "提交", "办理", "发起",
    "做工单", "提工单", "下单", "开工单",
    "repair", "maintenance", "submit", "apply", "create ticket",
    "create work order", "open ticket",
]

_TOOL_ACTION_KEYWORDS = [
    "计算", "算一下", "帮我算", "乘以", "除以", "加上", "减去",
    "等于多少", "换算", "转换", "查时间", "当前时间", "现在几点",
    "calculate", "compute", "multiply", "divide", "plus", "minus",
    "convert", "current time", "timestamp",
]

_ARITHMETIC_PATTERNS = [
    (re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:乘以|乘|x|X|\*)\s*(-?\d+(?:\.\d+)?)"), "multiply"),
    (re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:除以|除|/)\s*(-?\d+(?:\.\d+)?)"), "divide"),
    (re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:加上|加|\+)\s*(-?\d+(?:\.\d+)?)"), "add"),
    (re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:减去|减|-)\s*(-?\d+(?:\.\d+)?)"), "subtract"),
]

# ── Greeting / chitchat fast-path detection ───────────────

_GREETING_PREFIXES = [
    "你好", "您好", "嗨", "hi", "hello", "hey", "哈喽", "嘿",
    "早上好", "下午好", "晚上好", "早安", "晚安",
    "good morning", "good afternoon", "good evening",
]

_GREETING_EXACT = {
    "你是谁", "你是什么", "介绍一下你自己", "你能做什么",
    "what can you do", "who are you",
}


def _is_chitchat(message: str) -> bool:
    """Fast check: is this a PURE greeting with no real question attached?

    "你好" → True (pure greeting)
    "你好，我想报修" → False (greeting + real request)
    "你是谁" → True (about the bot itself)
    """
    msg = message.strip().lower()
    # Strip trailing punctuation for matching
    cleaned = msg.rstrip("?？!！。.~，, ")
    if any(kw in cleaned for kw in _WORKFLOW_ACTION_KEYWORDS + _TOOL_ACTION_KEYWORDS):
        return False
    if len(cleaned) <= 2:
        return True
    # Exact matches (e.g. "你是谁")
    if cleaned in _GREETING_EXACT:
        return True
    # Check if message is ONLY a greeting prefix (possibly with punctuation)
    for p in _GREETING_PREFIXES:
        if cleaned == p:
            return True
        # "你好呀" / "你好啊" — greeting + filler particle, still chitchat
        if cleaned.startswith(p) and len(cleaned) - len(p) <= 2:
            remainder = cleaned[len(p):]
            if not remainder or all(c in "呀啊呢哦哈吖嘛噢耶" for c in remainder):
                return True
    return False


# ── Multi-turn query rewriting ─────────────────────────────────

_CONTEXT_DEPENDENT_PATTERNS = [
    "这个", "那个", "它", "他", "她", "他们", "她们", "它们",
    "上面", "前面", "刚才", "之前", "上述", "上面说的",
    "还有吗", "还有呢", "继续", "然后呢", "接下来",
    "为什么", "怎么回事",
    "this", "that", "it", "them", "above", "previous",
    "more", "continue", "why", "how come",
]


def _needs_query_rewrite(message: str) -> bool:
    """Check if a message likely refers to prior conversation context."""
    msg = message.strip().lower()
    if len(msg) <= 6:
        return True
    for pattern in _CONTEXT_DEPENDENT_PATTERNS:
        if pattern in msg:
            return True
    return False


def _rewrite_query_with_history(message: str, history_messages: list) -> str:
    """Rewrite a context-dependent query by prepending recent conversation context."""
    if not history_messages:
        return message
    last_user = None
    for msg in reversed(history_messages):
        if msg.role == "user":
            last_user = msg.content
            break
    if last_user:
        return f"{last_user} {message}"
    return message


# ═════════════════════════════════════════════════════════════════

def _build_retrieval_fallback_answer(context: str, citations: list[Citation], limit: int = 2) -> str:
    """Build a concise customer-service answer from retrieved snippets."""
    parts: list[str] = []
    for idx, citation in enumerate(citations[:limit], start=1):
        snippet = (citation.content_snippet or "").strip()
        if not snippet:
            continue
        source = (citation.source_name or "knowledge base").strip()
        parts.append(f"[{idx}] {snippet} [source: {source}]")

    if parts:
        return "\n".join(parts)

    return (context or "").strip()[:500]


def _compact_citations(citations: list[Citation], max_total: int = 5) -> list[Citation]:
    """Return user-facing citations with at most one best hit per source."""
    best_by_source: dict[str, Citation] = {}
    source_order: list[str] = []

    for citation in citations:
        source_key = (citation.source_id or "").strip()
        if not source_key:
            source_key = (citation.source_name or "").strip().lower()
        if not source_key:
            source_key = "|".join([
                str(citation.page or ""),
                str(citation.paragraph or ""),
                (citation.content_snippet or "").strip().lower()[:120],
            ])

        existing = best_by_source.get(source_key)
        if existing is None:
            best_by_source[source_key] = citation
            source_order.append(source_key)
            continue

        citation_score = citation.score if citation.score is not None else -1.0
        existing_score = existing.score if existing.score is not None else -1.0
        if citation_score > existing_score:
            best_by_source[source_key] = citation

    return [best_by_source[key] for key in source_order[:max_total]]


def _retrieval_hits_payload(retrieval) -> dict:
    """Small trace payload for retrieval events."""
    return {
        "query": getattr(retrieval, "query", None),
        "hits": [
            {
                "source_id": hit.source_id,
                "source_name": hit.source_name,
                "score": hit.score,
                "channel": hit.channel,
                "snippet": (hit.content or "")[:160],
            }
            for hit in (getattr(retrieval, "hits", None) or [])[:5]
        ],
    }


class AgentRuntime:
    """Main orchestrator for handling user requests.

    Conversation-first architecture: every message goes to the LLM,
    which decides whether to respond directly or call skill-tools.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        # Outbound workflow events queued during this request, dispatched only
        # after the session state is committed (avoids phantom events for
        # transitions a later pipeline failure would roll back).
        self._pending_events: list[tuple[str, str, dict]] = []

    def _flush_pending_events(self) -> None:
        events, self._pending_events = self._pending_events, []
        for tenant_id, event_type, payload in events:
            emit_event(tenant_id, event_type, payload)

    @staticmethod
    def _append_agent_specific_instruction(base_desc: str, trigger_config: dict | None) -> str:
        override = (trigger_config or {}).get("trigger_description", "")
        override = override.strip() if isinstance(override, str) else ""
        if not override:
            return base_desc
        return f"{base_desc}\nAgent-specific instruction: {override}"

    async def _load_workflow_for_description(self, workflow_id: str) -> Workflow | None:
        if not self.db or not workflow_id:
            return None
        try:
            result = await self.db.execute(select(Workflow).where(Workflow.id == workflow_id))
            return result.scalar_one_or_none()
        except Exception:
            logger.warning("Failed to load workflow description for %s", workflow_id, exc_info=True)
            return None

    @staticmethod
    def _workflow_function_description(workflow: Workflow | None, fallback: str) -> str:
        if workflow is None:
            return fallback
        if workflow.description:
            return f"{workflow.name}: {workflow.description}"
        return f"Start workflow: {workflow.name}"

    # ── Main invoke entry point ──────────────────────────────

    async def invoke(
        self, req: InvokeRequest, event_cb: EventCallback | None = None,
    ) -> InvokeResponse:
        """Full pipeline: one user message in, one structured response out.

        `event_cb`, if given, is an optional `async (event_type, data) -> None`
        callback invoked for real-time progress events (pre_retrieval,
        tool_call_started/finished, answer_delta) during the conversational
        pipeline. It is purely additive and backward compatible — omitting it
        (the default) reproduces today's behavior exactly. Callback failures
        are caught and logged; they never interrupt the pipeline.
        """

        # 1. Load agent config
        agent = await self._load_agent(req.agent_id)
        if agent is None:
            return self._error_response("Agent 不存在或已停用", req)

        # 2. Get or create session
        session = await self._get_or_create_session(req, agent)

        # 3. Init audit
        trace_id = new_trace_id()
        audit = AuditLogger(self.db, trace_id, session.id, agent.id, agent.tenant_id)
        audit.log("user_input", event_data={"message": req.message, "form_data": req.form_data})

        try:
            # 4. Risk pre-check
            risk_block = self._risk_precheck(agent, req.message)
            if risk_block:
                audit.log("risk_block", event_data={"reason": risk_block})
                return InvokeResponse(
                    session_id=session.id,
                    trace_id=trace_id,
                    short_answer=risk_block,
                    suggested_followups=["换个问题试试？"],
                )

            # 5. Conversational pipeline
            return await self._invoke_conversational(
                agent, session, req, trace_id, audit, event_cb=event_cb,
            )
        finally:
            # Audit must survive unhandled pipeline exceptions; flush() writes
            # via an independent session and is a no-op when already flushed.
            await audit.flush()

    # ── Conversational invocation ─────────────────────────────

    async def _invoke_conversational(
        self, agent: Agent, session: ConversationSession,
        req: InvokeRequest, trace_id: str, audit: AuditLogger,
        event_cb: EventCallback | None = None,
    ) -> InvokeResponse:
        """Conversation-first pipeline: LLM drives, skills exposed as tools."""

        msg_lower = req.message.strip().lower()

        # ── Safety net: if workflow already completed, clear stale state ──
        wf_state = session.workflow_state or {}
        if wf_state.get("status") in ("completed", "cancelled"):
            session.workflow_state = None
            session.active_skill_id = None
            wf_state = {}

        # ── Fast-path: Active workflow bypass ──
        if wf_state.get("status") in (
            "in_progress", "waiting_input", "await_retry", "paused_for_review",
        ):
            if await self._should_exit_active_workflow(agent, req, audit):
                return await self._cancel_active_workflow(
                    agent, session, req, trace_id, audit,
                )
            # Continue workflow directly (no LLM routing needed)
            return await self._continue_active_workflow(
                agent, session, req, trace_id, audit,
            )

        # ── Fast-path: greeting / chitchat — skip retrieval entirely ──
        is_chitchat = _is_chitchat(msg_lower)
        if is_chitchat:
            audit.log("chitchat_detected", event_data={"message": req.message})

        # ── Load all skills ONCE for this request (avoid 3x DB queries) ──
        all_skills = await self._load_agent_skills(agent.id)

        # ── Check if user intends an action (workflow/tool) ──
        action_intent = ActionIntent()
        if not is_chitchat:
            action_intent = self._detect_action_intent_from_skills(
                all_skills, msg_lower, req.intent,
            )
            if action_intent.detected:
                audit.log("action_intent_detected", event_data={
                    "message": req.message,
                    "intent_type": action_intent.kind,
                    "matched": action_intent.matched,
                    "skip_pre_retrieval": True,
                })

        # ── Pre-retrieval for knowledge skills ──
        pre_context = ""
        pre_citations: list[Citation] = []
        if not action_intent.detected and not is_chitchat:
            knowledge_skills = [s for s in all_skills if s.skill_type == "knowledge_qa"]
            if knowledge_skills:
                # Use rewritten query for better retrieval
                history = await self._get_history(session.id, limit=RECENT_WINDOW)
                retrieval_query = req.message
                if _needs_query_rewrite(req.message) and history:
                    retrieval_query = await self._llm_rewrite_query(
                        agent, req.message, history, audit,
                    )
                domains = [(s.execution_config or {}).get("domain") for s in knowledge_skills]
                await _emit_event(event_cb, "pre_retrieval", {"domains": domains})
                pre_context, pre_citations = await self._pre_retrieve(
                    retrieval_query, knowledge_skills, audit,
                )
                await _emit_event(event_cb, "pre_retrieval_done", {"hits": len(pre_citations)})

        # ── Build skill tools (using pre-loaded skills) ──
        tool_defs, handler_map = await self._build_skill_tools(
            agent, session, audit,
            pre_retrieved=bool(pre_context),
            preloaded_skills=all_skills,
        )

        audit.log("conversational_init", event_data={
            "tools_count": len(tool_defs),
            "tool_names": [t["function"]["name"] for t in tool_defs],
        })

        # ── Build messages ──
        persona = agent.system_prompt or "你是一个智能助手。"

        # Rolling summary: cheap read of whatever a PRIOR turn's background
        # fold already persisted onto session.context["summary"]. The fold
        # itself runs off the critical path — see schedule_summary_update()
        # below, invoked after this turn's answer is saved.
        summary_text = await self._get_persisted_summary(agent, session, audit)
        # Cross-session memory seam (not implemented this wave — see method
        # docstring). Always None today; kept as a single call site so a
        # future wave can light it up without restructuring this method.
        longterm_memory = self._load_longterm_memory(agent, req)

        history = await self._get_history(session.id, limit=RECENT_WINDOW)

        if pre_context:
            system_msg = CONVERSATIONAL_SYSTEM_PROMPT_WITH_CONTEXT.format(
                persona=persona, context=pre_context,
            )
        else:
            system_msg = CONVERSATIONAL_SYSTEM_PROMPT.format(persona=persona)

        messages: list[LLMMessage] = [LLMMessage(role="system", content=system_msg)]

        # Prepend context messages from parent agent delegation (if any)
        if req.context_messages:
            for ctx_msg in req.context_messages:
                messages.append(LLMMessage(
                    role=ctx_msg.get("role", "user"),
                    content=ctx_msg.get("content", ""),
                ))

        if longterm_memory:
            messages.append(LLMMessage(role="system", content=longterm_memory))

        if summary_text:
            messages.append(LLMMessage(role="system", content=f"[对话摘要] {summary_text}"))

        for msg in history:
            messages.append(LLMMessage(role=msg.role, content=msg.content))

        # When action intent is detected, add an instruction hint so the LLM
        # calls the tool instead of answering with text
        user_content = req.message
        if action_intent.detected:
            user_content = (
                f"{req.message}\n"
                f"[系统：检测到{action_intent.kind or 'action'}请求，请调用最匹配的工具处理，不要用文字直接代办。]"
            )
        messages.append(LLMMessage(role="user", content=user_content))

        if req.expand:
            messages.append(LLMMessage(role="user", content="请给出详细完整的回答。"))

        # ── Multi-round function calling loop ──
        llm = await get_llm_adapter_for_agent(agent, self.db)

        collected_citations = list(pre_citations)
        collected_workflow_card: WorkflowCard | None = None
        collected_workflow_status: str | None = None
        collected_skill_info: list[dict] = []
        tool_calls_log: list[dict] = []
        fallback_info: dict | None = None
        final_content = ""
        llm_resp = None
        last_tool_result_text = ""

        for round_idx in range(MAX_TOOL_ROUNDS):
            audit.start_timer(f"llm_round_{round_idx}")
            messages = trim_messages(messages, MAX_INPUT_TOKENS)

            try:
                llm_resp = None
                if event_cb is not None and hasattr(llm, "chat_stream"):
                    try:
                        llm_resp = await self._stream_round(
                            llm, messages, tool_defs, event_cb,
                        )
                    except LLMStreamError as stream_exc:
                        logger.warning(
                            f"chat_stream failed for round {round_idx}, "
                            f"falling back to non-streaming call: {stream_exc}",
                        )
                        llm_resp = None
                if llm_resp is None:
                    if tool_defs:
                        llm_resp = await llm.chat_with_tools(messages, tool_defs)
                    else:
                        llm_resp = await llm.chat(messages)
            except Exception as e:
                audit.log("error", event_data={
                    "error": str(e), "round": round_idx, "stage": "llm_call",
                })
                if action_intent.detected and not tool_calls_log:
                    fallback = await self._execute_action_fallback(
                        action_intent, req.message, handler_map, tool_defs, audit,
                    )
                    if fallback:
                        fn_name, arguments, result = fallback
                        if result.citations:
                            collected_citations.extend(result.citations)
                        if result.workflow_card:
                            collected_workflow_card = result.workflow_card
                            collected_workflow_status = result.workflow_status
                        if result.skill_info:
                            collected_skill_info.append(result.skill_info)
                        tool_calls_log.append({
                            "function": fn_name,
                            "arguments": arguments,
                            "fallback": True,
                            "fallback_reason": "llm_call_failed",
                        })
                        final_content = result.text
                        break
                if tool_calls_log and last_tool_result_text:
                    final_content = last_tool_result_text
                    fallback_info = {
                        "type": "tool_result",
                        "reason": "llm_call_failed_after_tool_call",
                    }
                    break
                if pre_citations:
                    # Have retrieval data — use it as fallback
                    final_content = _build_retrieval_fallback_answer(pre_context, pre_citations)
                    fallback_info = {
                        "type": "retrieval",
                        "reason": "llm_call_failed",
                        "citations_count": len(pre_citations),
                    }
                    audit.log("retrieval_fallback", event_data=fallback_info)
                    break
                await audit.flush()
                return self._fallback_response(session.id, trace_id, str(e))

            round_latency = audit.elapsed_ms(f"llm_round_{round_idx}")
            audit.log("llm_call", llm_meta={
                "model": llm_resp.model,
                "round": round_idx + 1,
                "has_tool_calls": bool(llm_resp.tool_calls),
                # Token usage — parsed from the provider's `usage` field in
                # LLMAdapter.chat()/chat_with_tools(); for the streaming path
                # (chat_stream) this is best-effort since not every provider
                # emits a `usage` block on the final SSE chunk. Both default
                # to 0 when absent, which the usage aggregation endpoint
                # (GET /audit/usage) treats as "no data" rather than an error.
                "prompt_tokens": llm_resp.prompt_tokens,
                "completion_tokens": llm_resp.completion_tokens,
                "total_tokens": llm_resp.prompt_tokens + llm_resp.completion_tokens,
            }, latency_ms=round_latency)

            # No tool calls → final answer
            if not llm_resp.tool_calls:
                if action_intent.detected and not tool_calls_log:
                    fallback = await self._execute_action_fallback(
                        action_intent, req.message, handler_map, tool_defs, audit,
                    )
                    if fallback:
                        fn_name, arguments, result = fallback
                        if result.citations:
                            collected_citations.extend(result.citations)
                        if result.workflow_card:
                            collected_workflow_card = result.workflow_card
                            collected_workflow_status = result.workflow_status
                        if result.skill_info:
                            collected_skill_info.append(result.skill_info)
                        tool_calls_log.append({
                            "function": fn_name,
                            "arguments": arguments,
                            "fallback": True,
                            "fallback_reason": "llm_no_tool_call",
                        })
                        final_content = result.text
                        break
                final_content = (llm_resp.content or "").strip() or last_tool_result_text
                break

            # LLM wants to call tools
            raw_tool_calls = [tc.raw for tc in llm_resp.tool_calls]
            messages.append(LLMMessage(
                role="assistant",
                content=llm_resp.content or "",
                tool_calls=raw_tool_calls,
            ))

            for tc in llm_resp.tool_calls:
                handler = handler_map.get(tc.function_name)
                if handler is None:
                    messages.append(LLMMessage(
                        role="tool",
                        content=json.dumps({"error": f"Unknown tool: {tc.function_name}"}),
                        tool_call_id=tc.id,
                    ))
                    continue

                await _emit_event(event_cb, "tool_call_started", {
                    "name": tc.function_name, "round": round_idx,
                })
                audit.start_timer(f"tool_{tc.id}")
                tool_ok = False
                try:
                    result: SkillToolResult = await handler(tc.arguments)
                    tool_ok = True
                except Exception as e:
                    logger.warning(f"Skill tool handler error [{tc.function_name}]: {e}")
                    result = SkillToolResult(text=f"Error: {e}")
                await _emit_event(event_cb, "tool_call_finished", {
                    "name": tc.function_name, "round": round_idx, "ok": tool_ok,
                })

                tool_latency = audit.elapsed_ms(f"tool_{tc.id}")
                audit.log("tool_call", tool_meta={
                    "function_name": tc.function_name,
                    "arguments": tc.arguments,
                    "result_preview": result.text[:200],
                    "round": round_idx,
                }, latency_ms=tool_latency)

                messages.append(LLMMessage(
                    role="tool",
                    content=truncate_text(result.text, MAX_TOOL_RESULT_TOKENS),
                    tool_call_id=tc.id,
                ))
                if result.text:
                    last_tool_result_text = result.text

                # Collect side effects
                if result.citations:
                    collected_citations.extend(result.citations)
                if result.workflow_card:
                    collected_workflow_card = result.workflow_card
                    collected_workflow_status = result.workflow_status
                if result.skill_info:
                    collected_skill_info.append(result.skill_info)

                tool_calls_log.append({
                    "function": tc.function_name,
                    "arguments": tc.arguments,
                })
        else:
            # Exhausted all rounds without a final answer
            final_content = (llm_resp.content if llm_resp else "") or ""

        # ── Build response ──
        short_answer = (final_content or "").strip()

        # Fallback if empty
        if not short_answer:
            if pre_citations:
                short_answer = _build_retrieval_fallback_answer(pre_context, pre_citations)
                fallback_info = {
                    "type": "retrieval",
                    "reason": "empty_llm_response",
                    "citations_count": len(pre_citations),
                }
            elif pre_context:
                short_answer = pre_context[:500]
            elif tool_calls_log:
                short_answer = "操作已完成。"
            else:
                short_answer = "抱歉，未能获取到有效回复，请重试。"

        # Refusal + citations: append labeled reference, never silently replace
        short_answer, refusal_supplemented = _apply_refusal_supplement(
            short_answer, collected_citations,
        )
        if refusal_supplemented:
            audit.log("refusal_supplement", event_data={"citations": len(collected_citations)})

        user_facing_citations = _compact_citations(collected_citations)

        # Save messages
        await self._save_message(session.id, "user", req.message, trace_id)
        await self._save_message(session.id, "assistant", short_answer, trace_id)
        await self._save_session(session)
        # Session state is committed — safe to dispatch queued workflow events.
        self._flush_pending_events()

        # Rolling summary fold happens off the critical path: this turn's
        # rows are now committed, so schedule a background fold (deduped,
        # non-raising) that the NEXT turn's prompt will see — see
        # server.engine.summary_scheduler for the one-turn-lag rationale.
        if (session.message_count or 0) * 2 >= SUMMARY_THRESHOLD:
            schedule_summary_update(session.id, agent.id)
            audit.log("summary_scheduled", event_data={
                "message_count": session.message_count,
            })

        # Followups
        if collected_workflow_card:
            followups = ["如何填写？", "需要准备什么材料？"]
        elif user_facing_citations:
            followups = ["需要更多详细信息吗？", "还有其他问题吗？"]
        elif tool_calls_log:
            followups = ["查看详细结果", "还有其他问题吗？"]
        else:
            followups = ["有什么可以帮助你的吗？"]

        audit.log("response", event_data={
            "short_answer": short_answer[:200],
            "mode": "conversational",
            "tool_calls_count": len(tool_calls_log),
            "citations_count": len(user_facing_citations),
            "raw_citations_count": len(collected_citations),
            "fallback": fallback_info,
        })
        await audit.flush()

        metadata = {"mode": "conversational"}
        if tool_calls_log:
            metadata["tool_calls"] = tool_calls_log
        if fallback_info:
            metadata["fallback"] = fallback_info
            metadata["degraded"] = True
        if refusal_supplemented:
            metadata["refusal_supplemented"] = True

        return InvokeResponse(
            session_id=session.id,
            trace_id=trace_id,
            short_answer=short_answer,
            citations=user_facing_citations,
            suggested_followups=followups,
            workflow_card=collected_workflow_card,
            workflow_status=collected_workflow_status,
            skill_info=collected_skill_info[0] if collected_skill_info else None,
            metadata=metadata if len(metadata) > 1 else None,
        )

    async def _stream_round(
        self, llm, messages: list[LLMMessage], tool_defs: list[dict],
        event_cb: EventCallback,
    ):
        """Run one function-calling round via chat_stream.

        Content deltas are forwarded live as `answer_delta` events the moment
        they arrive — clients can render tokens as they stream in. This means
        a round can emit text that turns out to be throwaway, so an
        `answer_reset` event tells the client to discard whatever partial text
        it has accumulated for the round in two cases:
          1. The round ends in tool_calls: any content streamed before the
             LLM decided to call a tool was never meant to be user-facing.
          2. The stream fails mid-way (raises, or ends without a final chunk):
             the non-streaming fallback will produce the real answer, and the
             client must not append it to stale partial text.
        The final SSE `answer` event (emitted by the caller once the whole
        response is assembled) remains authoritative regardless of what was
        streamed here. Raises LLMStreamError if the stream never produces a
        final chunk (after emitting `answer_reset` if deltas were sent).
        """
        final_response = None
        emitted = False
        try:
            async for chunk in llm.chat_stream(messages, tool_defs or None):
                if chunk.delta:
                    emitted = True
                    await _emit_event(event_cb, "answer_delta", {"text": chunk.delta})
                if chunk.done:
                    final_response = chunk.response
        except Exception:
            if emitted:
                await _emit_event(event_cb, "answer_reset", {})
            raise

        if final_response is None:
            if emitted:
                await _emit_event(event_cb, "answer_reset", {})
            raise LLMStreamError("chat_stream ended without a final response")

        if final_response.tool_calls and emitted:
            await _emit_event(event_cb, "answer_reset", {})
        return final_response

    # ── Build skill tools ────────────────────────────────────

    async def _build_skill_tools(
        self, agent: Agent, session: ConversationSession, audit: AuditLogger,
        pre_retrieved: bool = False,
        preloaded_skills: list | None = None,
    ) -> tuple[list[dict], dict[str, Callable]]:
        """Convert agent's bound skills to OpenAI function defs + handler map.

        Skill type → Tool mapping:
          knowledge_qa → search_knowledge / search_knowledge_{domain}
          tool_call    → flatten individual HTTP tools
          workflow     → start_workflow_{name}
          delegate     → delegate_to_{agent_name}
          composite    → recurse into sub-skills
        """

        skills = preloaded_skills if preloaded_skills is not None else await self._load_agent_skills(agent.id)
        if not skills:
            return [], {}

        tool_defs: list[dict] = []
        handler_map: dict[str, Callable] = {}
        used_names: set[str] = set()

        def _unique_name(base: str) -> str:
            name = self._sanitize_function_name(base)
            if name not in used_names:
                used_names.add(name)
                return name
            i = 2
            while f"{name}_{i}" in used_names:
                i += 1
            unique = f"{name}_{i}"
            used_names.add(unique)
            return unique

        for skill in skills:
            config = skill.execution_config or {}

            if skill.skill_type == "knowledge_qa":
                await self._register_knowledge_tool(
                    skill, config, tool_defs, handler_map, _unique_name,
                    audit, pre_retrieved=pre_retrieved,
                )

            elif skill.skill_type == "tool_call":
                await self._register_http_tools(
                    skill, config, tool_defs, handler_map, _unique_name, audit,
                )

            elif skill.skill_type == "workflow":
                await self._register_workflow_tool(
                    skill, config, tool_defs, handler_map, _unique_name,
                    session, audit,
                )

            elif skill.skill_type == "delegate":
                await self._register_delegate_tool(
                    skill, config, tool_defs, handler_map, _unique_name,
                    agent, session, audit,
                )

            elif skill.skill_type == "composite":
                await self._register_composite_tools(
                    skill, config, tool_defs, handler_map, _unique_name,
                    session, audit,
                )

        return tool_defs, handler_map

    # ── Skill-tool registration helpers ──────────────────────

    async def _register_knowledge_tool(
        self, skill: Skill, config: dict,
        tool_defs: list, handler_map: dict,
        unique_name: Callable[[str], str],
        audit: AuditLogger,
        pre_retrieved: bool = False,
    ) -> None:
        domain = config.get("domain", "default")
        name = unique_name(
            f"search_knowledge_{domain}" if domain != "default" else "search_knowledge"
        )
        base_desc = skill.description or f"搜索知识库 ({domain}) 获取相关信息"
        base_desc = self._append_agent_specific_instruction(base_desc, skill.trigger_config)
        if pre_retrieved:
            desc = base_desc + "（已有初始结果，仅需重新搜索时调用）"
        else:
            desc = base_desc

        tool_defs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索查询关键词",
                        },
                    },
                    "required": ["query"],
                },
            },
        })
        handler_map[name] = self._make_knowledge_handler(skill, config, audit)

    async def _register_http_tools(
        self, skill: Skill, config: dict,
        tool_defs: list, handler_map: dict,
        unique_name: Callable[[str], str],
        audit: AuditLogger,
    ) -> None:
        """Flatten: register each HTTP tool individually."""
        tool_ids = config.get("tool_ids", [])
        if not tool_ids:
            return

        http_tools, http_tool_map = await self._load_tools_as_functions(tool_ids)
        for tool_def in http_tools:
            orig_fn_name = tool_def["function"]["name"]
            fn_name = unique_name(orig_fn_name)
            function_def = {
                **tool_def["function"],
                "name": fn_name,
                "description": self._append_agent_specific_instruction(
                    tool_def["function"].get("description", ""),
                    skill.trigger_config,
                ),
            }
            tool_def = {**tool_def, "function": function_def}
            tool_defs.append(tool_def)

            tool_definition = http_tool_map.get(orig_fn_name)
            if tool_definition:
                handler_map[fn_name] = self._make_http_tool_handler(
                    tool_definition, audit,
                )

    async def _register_workflow_tool(
        self, skill: Skill, config: dict,
        tool_defs: list, handler_map: dict,
        unique_name: Callable[[str], str],
        session: ConversationSession, audit: AuditLogger,
    ) -> None:
        workflow_id = config.get("workflow_id")
        if not workflow_id:
            return

        clean_name = self._sanitize_function_name(
            skill.name.replace("[auto]", "").strip()
        )
        if clean_name and clean_name != "unnamed":
            name = unique_name(f"start_workflow_{clean_name}")
        else:
            name = unique_name(f"start_workflow_{workflow_id[:8]}")
        workflow = await self._load_workflow_for_description(workflow_id)
        fallback_desc = skill.description or f"Start workflow: {skill.name}"
        base_desc = self._workflow_function_description(workflow, fallback_desc)
        base_desc = self._append_agent_specific_instruction(base_desc, skill.trigger_config)
        trigger_kw = (skill.trigger_config or {}).get("keywords", [])
        desc = f"{base_desc}（{', '.join(trigger_kw)}）" if trigger_kw else base_desc

        tool_defs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "用户想要办理此业务的原因或备注（可选）",
                        },
                    },
                    "required": [],
                },
            },
        })
        handler_map[name] = self._make_workflow_handler(
            skill, config, session, audit,
        )

    async def _register_delegate_tool(
        self, skill: Skill, config: dict,
        tool_defs: list, handler_map: dict,
        unique_name: Callable[[str], str],
        source_agent: Agent, session: ConversationSession,
        audit: AuditLogger,
    ) -> None:
        target_agent_id = config.get("target_agent_id")
        if not target_agent_id:
            return

        target = await self._load_agent(target_agent_id)
        target_name = target.name if target else target_agent_id[:8]
        sanitized = self._sanitize_function_name(target_name)
        if sanitized == "unnamed":
            sanitized = target_agent_id[:8]
        fn_name = unique_name(f"delegate_to_{sanitized}")
        desc = skill.description or f"将问题委派给 {target_name} 处理"

        tool_defs.append({
            "type": "function",
            "function": {
                "name": fn_name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "要转发给目标Agent的消息",
                        },
                    },
                    "required": ["message"],
                },
            },
        })
        handler_map[fn_name] = self._make_delegate_handler(
            skill, config, source_agent, session, audit,
        )

    async def _register_composite_tools(
        self, skill: Skill, config: dict,
        tool_defs: list, handler_map: dict,
        unique_name: Callable[[str], str],
        session: ConversationSession, audit: AuditLogger,
    ) -> None:
        """Decompose a composite skill into its sub-skill tools."""
        sub_skill_ids = config.get("sub_skill_ids", [])
        if not sub_skill_ids:
            return

        result = await self.db.execute(
            select(Skill).where(
                Skill.id.in_(sub_skill_ids),
                Skill.enabled.is_(True),
            )
        )
        sub_skills = list(result.scalars().all())

        for sub_skill in sub_skills:
            sub_config = sub_skill.execution_config or {}

            if sub_skill.skill_type == "knowledge_qa":
                await self._register_knowledge_tool(
                    sub_skill, sub_config, tool_defs, handler_map, unique_name, audit,
                )
            elif sub_skill.skill_type == "tool_call":
                await self._register_http_tools(
                    sub_skill, sub_config, tool_defs, handler_map,
                    unique_name, audit,
                )
            elif sub_skill.skill_type == "workflow":
                await self._register_workflow_tool(
                    sub_skill, sub_config, tool_defs, handler_map,
                    unique_name, session, audit,
                )

    # ── Skill-tool handlers ──────────────────────────────────

    def _make_knowledge_handler(
        self, skill: Skill, config: dict, audit: AuditLogger,
    ) -> Callable[[dict], Awaitable[SkillToolResult]]:
        """Create a handler that searches knowledge for a query."""
        domain = config.get("domain")

        async def handler(args: dict) -> SkillToolResult:
            query = args.get("query", "")
            retriever = KnowledgeRetriever(
                self.db, vector_store=get_vector_store_if_initialized(),
                runtime_cfg=runtime_config.all(),
            )
            try:
                retrieval = await retriever.retrieve(query, domain=domain, top_k=5)
            except Exception as e:
                logger.warning(f"Knowledge retrieval failed: {e}")
                return SkillToolResult(text="知识库检索失败，请稍后重试。")

            audit.log(
                "retrieval",
                event_data={
                    "domain": domain,
                    "hit_count": len(retrieval.hits) if retrieval else 0,
                },
                retrieval_hits=_retrieval_hits_payload(retrieval) if retrieval else None,
                latency_ms=retrieval.latency_ms if retrieval else None,
            )

            if not retrieval or not retrieval.hits:
                return SkillToolResult(text="未找到相关信息。")

            parts = []
            citations = []
            for i, hit in enumerate(retrieval.hits[:5]):
                parts.append(f"[{i+1}] {truncate_text(hit.content, 800)}")
                citations.append(Citation(
                    source_id=hit.source_id or "",
                    source_name=hit.source_name or f"来源{i+1}",
                    content_snippet=hit.content[:200],
                    page=hit.page,
                    score=hit.score,
                ))

            return SkillToolResult(
                text="\n\n".join(parts),
                citations=citations,
                skill_info={"skill_id": skill.id, "skill_type": "knowledge_qa"},
            )

        return handler

    def _make_http_tool_handler(
        self, tool_def: ToolDefinition, audit: AuditLogger,
    ) -> Callable[[dict], Awaitable[SkillToolResult]]:
        """Create a handler that invokes an HTTP tool."""
        async def handler(args: dict) -> SkillToolResult:
            tool_gw = ToolGateway(self.db, audit)
            try:
                result = await tool_gw.invoke(tool_def.id, args)
                text = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                text = truncate_text(text, MAX_TOOL_RESULT_TOKENS)
                return SkillToolResult(text=text)
            except Exception as e:
                return SkillToolResult(text=f"工具调用失败: {e}")

        return handler

    def _make_workflow_handler(
        self, skill: Skill, config: dict,
        session: ConversationSession, audit: AuditLogger,
    ) -> Callable[[dict], Awaitable[SkillToolResult]]:
        """Create a handler that starts a workflow."""
        async def handler(args: dict) -> SkillToolResult:
            workflow_id = config.get("workflow_id")
            session.workflow_state = {
                "workflow_id": workflow_id,
                "current_step_index": 0,
                "status": "in_progress",
                # One idempotency key per workflow run; tool_call and the
                # complete-step webhook both forward it so retried
                # submissions can be deduplicated by the receiving end.
                "idempotency_key": str(uuid.uuid4()),
            }
            flag_modified(session, "workflow_state")
            session.active_skill_id = skill.id
            # Hygiene: never let a previous ticket's collected data bleed
            # into this new workflow run.
            session.collected_data = {}
            flag_modified(session, "collected_data")

            tool_gw = ToolGateway(self.db, audit)
            executor = WorkflowExecutor(self.db, tool_gw, audit)

            try:
                result = await executor.process_step(
                    session, args.get("reason", ""), None,
                )
            except Exception as e:
                session.workflow_state = None
                session.active_skill_id = None
                return SkillToolResult(text=f"流程启动失败: {e}")

            # Defer any queued events until the conversational tail commits.
            self._pending_events.extend(executor.pending_events)

            return SkillToolResult(
                text=result.message,
                workflow_card=result.card,
                workflow_status=result.status,
                skill_info={
                    "skill_id": skill.id,
                    "skill_type": "workflow",
                    "workflow_id": workflow_id,
                },
            )

        return handler

    def _make_delegate_handler(
        self, skill: Skill, config: dict,
        source_agent: Agent, session: ConversationSession,
        audit: AuditLogger,
    ) -> Callable[[dict], Awaitable[SkillToolResult]]:
        """Create a handler that delegates to another agent."""
        async def handler(args: dict) -> SkillToolResult:
            target_agent_id = config.get("target_agent_id")
            user_message = args.get("message", "")

            # Cycle and depth protection
            chain = list(session.delegation_chain or [])
            if target_agent_id in chain:
                return SkillToolResult(text="检测到循环委派，无法继续。")
            if len(chain) >= MAX_DELEGATION_DEPTH:
                return SkillToolResult(
                    text=f"委派深度超过限制 ({MAX_DELEGATION_DEPTH})。",
                )

            chain.append(source_agent.id)
            session.delegation_chain = chain

            # Carry last N messages as context for the delegated agent
            context_limit = 5
            history = await self._get_history(session.id, limit=context_limit)
            context_msgs = [
                {"role": msg.role, "content": msg.content}
                for msg in history  # already oldest-first from _get_history
            ]

            audit.log("delegation", event_data={
                "from_agent": source_agent.id,
                "to_agent": target_agent_id,
                "depth": len(chain),
                "context_messages_count": len(context_msgs),
            })

            delegated_req = InvokeRequest(
                agent_id=target_agent_id,
                session_id=None,
                user_id="delegated",
                message=user_message,
                context_messages=context_msgs if context_msgs else None,
                parent_session_id=session.id,
                shared_context=dict(session.shared_context or {}),
            )

            runtime = AgentRuntime(self.db)
            try:
                response = await runtime.invoke(delegated_req)
            except Exception as e:
                return SkillToolResult(text=f"委派失败: {e}")
            finally:
                session.delegation_chain = None

            return SkillToolResult(
                text=response.short_answer,
                citations=response.citations or None,
                skill_info={
                    "skill_id": skill.id,
                    "skill_type": "delegate",
                    "delegated_to": target_agent_id,
                },
            )

        return handler

    # ── Pre-retrieval ────────────────────────────────────────

    async def _execute_action_fallback(
        self,
        action_intent: ActionIntent,
        message: str,
        handler_map: dict[str, Callable],
        tool_defs: list[dict],
        audit: AuditLogger,
    ) -> tuple[str, dict, SkillToolResult] | None:
        """Execute a clear action when the LLM cannot or will not call tools.

        This is a guardrail, not the primary router. It only runs after an LLM
        failure/no-tool response and only for obvious workflow/tool requests.
        """
        if not handler_map:
            return None

        if action_intent.kind == "workflow":
            for fn_name, handler in handler_map.items():
                if fn_name.startswith("start_workflow_"):
                    args = {"reason": message}
                    result = await handler(args)
                    audit.log("action_fallback", event_data={
                        "intent_type": "workflow",
                        "function_name": fn_name,
                        "reason": "llm_unavailable_or_no_tool_call",
                    })
                    return fn_name, args, result
            return None

        if action_intent.kind != "tool":
            return None

        msg_lower = message.lower()
        tool_names = [
            tool_def.get("function", {}).get("name", "")
            for tool_def in tool_defs
        ]

        if any(kw in msg_lower for kw in ["计算", "算", "乘", "除", "加", "减", "calculate", "compute"]):
            calculator_name = next((name for name in tool_names if "calculator" in name.lower()), "")
            if calculator_name and calculator_name in handler_map:
                args = self._extract_calculator_args(message)
                if args:
                    result = await handler_map[calculator_name](args)
                    audit.log("action_fallback", event_data={
                        "intent_type": "tool",
                        "function_name": calculator_name,
                        "reason": "llm_unavailable_or_no_tool_call",
                    })
                    return calculator_name, args, result

        if any(kw in msg_lower for kw in ["时间", "日期", "几点", "time", "date", "timestamp"]):
            timestamp_name = next((name for name in tool_names if "timestamp" in name.lower()), "")
            if timestamp_name and timestamp_name in handler_map:
                args = {"format": "iso"}
                result = await handler_map[timestamp_name](args)
                audit.log("action_fallback", event_data={
                    "intent_type": "tool",
                    "function_name": timestamp_name,
                    "reason": "llm_unavailable_or_no_tool_call",
                })
                return timestamp_name, args, result

        return None

    @staticmethod
    def _extract_calculator_args(message: str) -> dict | None:
        """Extract simple arithmetic args for calculator fallback."""
        normalized = message.replace("，", " ").replace("。", " ")
        for pattern, operation in _ARITHMETIC_PATTERNS:
            match = pattern.search(normalized)
            if not match:
                continue
            a = float(match.group(1))
            b = float(match.group(2))
            return {
                "operation": operation,
                "a": int(a) if a.is_integer() else a,
                "b": int(b) if b.is_integer() else b,
            }

        expression_match = re.search(r"[-+*/().\d\s]{3,}", normalized)
        if expression_match:
            expression = expression_match.group(0).strip()
            if expression:
                return {"expression": expression}

        return None

    async def _get_knowledge_skills(self, agent_id: str) -> list[Skill]:
        """Get knowledge_qa skills bound to an agent."""
        skills = await self._load_agent_skills(agent_id)
        return [s for s in skills if s.skill_type == "knowledge_qa"]

    def _has_workflow_intent_from_skills(self, skills: list, msg_lower: str) -> bool:
        """Check workflow intent using pre-loaded skills (no DB call)."""
        for kw in _WORKFLOW_ACTION_KEYWORDS:
            if kw in msg_lower:
                return True
        for skill in skills:
            if skill.skill_type != "workflow":
                continue
            trigger_kws = (skill.trigger_config or {}).get("keywords", [])
            for kw in trigger_kws:
                if kw in msg_lower:
                    return True
        return False

    def _detect_action_intent_from_skills(
        self,
        skills: list,
        msg_lower: str,
        explicit_intent: str | None = None,
    ) -> ActionIntent:
        """Detect strong action intent without replacing LLM function calling.

        This only decides whether to skip knowledge pre-retrieval and strengthen
        the tool-use instruction. The LLM still selects and calls the function.
        """
        intent = (explicit_intent or "").strip().lower()
        if intent in {"workflow", "tool", "tool_call", "delegate"}:
            return ActionIntent(kind="tool" if intent == "tool_call" else intent, matched="explicit_intent")

        if self._has_workflow_intent_from_skills(skills, msg_lower):
            return ActionIntent(kind="workflow", matched="keyword")

        for skill in skills:
            if skill.skill_type != "tool_call":
                continue

            trigger_cfg = skill.trigger_config or {}
            trigger_keywords = trigger_cfg.get("keywords", []) or []
            for kw in trigger_keywords:
                if str(kw).lower() in msg_lower:
                    return ActionIntent(kind="tool", matched=str(kw))

            skill_text = " ".join(
                str(value)
                for value in [
                    skill.name,
                    skill.description or "",
                    trigger_cfg.get("trigger_description", ""),
                ]
                if value
            ).lower()

            if any(kw in msg_lower for kw in _TOOL_ACTION_KEYWORDS):
                return ActionIntent(kind="tool", matched="generic_tool_keyword")
            if skill_text and any(kw in msg_lower for kw in skill_text.split()):
                return ActionIntent(kind="tool", matched="skill_description")

        return ActionIntent()

    async def _has_workflow_intent(self, agent_id: str, msg_lower: str) -> bool:
        """Check if user message matches workflow skill trigger keywords.

        When the user's intent is clearly an action (repair, apply, submit),
        we skip pre-retrieval so the LLM isn't distracted by knowledge data
        and is more likely to call the workflow tool.
        """
        # Generic action keywords that always indicate workflow intent
        for kw in _WORKFLOW_ACTION_KEYWORDS:
            if kw in msg_lower:
                return True

        # Check workflow skill-specific keywords
        skills = await self._load_agent_skills(agent_id)
        for skill in skills:
            if skill.skill_type != "workflow":
                continue
            trigger_kws = (skill.trigger_config or {}).get("keywords", [])
            for kw in trigger_kws:
                if kw in msg_lower:
                    return True
        return False

    async def _pre_retrieve(
        self, message: str, knowledge_skills: list[Skill],
        audit: AuditLogger,
    ) -> tuple[str, list[Citation]]:
        """Run pre-retrieval across knowledge skills, return context + citations."""
        all_parts: list[str] = []
        all_citations: list[Citation] = []

        for skill in knowledge_skills:
            config = skill.execution_config or {}
            domain = config.get("domain")

            retriever = KnowledgeRetriever(
                self.db, vector_store=get_vector_store_if_initialized(),
                runtime_cfg=runtime_config.all(),
            )

            audit.start_timer("pre_retrieval")
            try:
                retrieval = await retriever.retrieve(message, domain=domain, top_k=5)
            except Exception as e:
                logger.warning(f"Pre-retrieval failed for domain {domain}: {e}")
                continue

            pre_latency = audit.elapsed_ms("pre_retrieval")
            audit.log("pre_retrieval", event_data={
                "domain": domain,
                "hit_count": len(retrieval.hits) if retrieval else 0,
            }, retrieval_hits=_retrieval_hits_payload(retrieval) if retrieval else None,
                latency_ms=pre_latency)

            if retrieval and retrieval.hits:
                for hit in retrieval.hits[:5]:
                    cite_info = f"[来源: {hit.source_name}"
                    if hit.page:
                        cite_info += f", 第{hit.page}页"
                    cite_info += "]"
                    idx = len(all_parts) + 1
                    all_parts.append(f"[{idx}] {hit.content} {cite_info}")
                    all_citations.append(Citation(
                        source_id=hit.source_id or "",
                        source_name=hit.source_name or f"来源{idx}",
                        content_snippet=hit.content[:200],
                        page=hit.page,
                        score=hit.score,
                    ))

        return "\n\n".join(all_parts), all_citations

    # ── Active workflow handlers ─────────────────────────────

    async def _should_exit_active_workflow(
        self, agent: Agent, req: InvokeRequest, audit: AuditLogger,
    ) -> bool:
        """Classify whether an active workflow turn means cancellation.

        Form submissions stay deterministic and bypass classification
        entirely. For natural-language turns, the local keyword/negation
        check runs FIRST and decides the vast majority of turns (most
        messages during an active workflow are just field data — phone
        numbers, names, dates — with zero ambiguity). The LLM is only
        called as a narrow fallback when the local check is inconclusive
        (no keyword/negation match) AND the message doesn't look like a
        plain field value, i.e. genuinely ambiguous free-form text.
        """
        if req.form_data:
            return False

        message = (req.message or "").strip()
        if not message:
            return False

        local_decision = _is_workflow_exit_intent(message)
        if local_decision:
            audit.log(
                "workflow_turn_intent",
                event_data={"message": message, "source": "local_keyword", "exit": True},
            )
            return True

        if _looks_like_plain_field_value(message):
            audit.log(
                "workflow_turn_intent",
                event_data={"message": message, "source": "local_field_data", "exit": False},
            )
            return False

        prompt = (
            "You are classifying a user's message while an enterprise workflow is active.\n"
            "Decide whether the user wants to cancel/leave the active workflow, or continue it.\n"
            "Return JSON only: {\"action\":\"cancel_workflow\"} or {\"action\":\"continue_workflow\"}.\n\n"
            "Use cancel_workflow when the user means: stop, never mind, no longer needs this, "
            "does not want to keep filling the form, wants to go back to normal chat, or wants to switch away.\n"
            "Use continue_workflow when the user provides field values, asks how to fill something, "
            "confirms, says no to edit a confirmation step, or explicitly says not to cancel.\n\n"
            f"User message: {message}"
        )

        try:
            llm = await get_llm_adapter_for_agent(agent, self.db)
            resp = await llm.chat(
                [
                    LLMMessage(role="system", content="Classify active workflow turn intent. Output JSON only."),
                    LLMMessage(role="user", content=prompt),
                ],
                temperature=0.0,
                max_tokens=80,
            )
            decision = _parse_workflow_exit_decision(resp.content)
            audit.log(
                "workflow_turn_intent",
                event_data={
                    "message": message,
                    "source": "llm",
                    "raw": resp.content[:200],
                    "exit": decision,
                },
            )
            if decision is not None:
                return decision
        except Exception as e:
            audit.log(
                "workflow_turn_intent",
                event_data={
                    "message": message,
                    "source": "llm_error",
                    "error": str(e),
                },
            )

        decision = _is_workflow_exit_intent(message)
        audit.log(
            "workflow_turn_intent",
            event_data={
                "message": message,
                "source": "local_fallback",
                "exit": decision,
            },
        )
        return decision

    async def _continue_active_workflow(
        self, agent: Agent, session: ConversationSession,
        req: InvokeRequest, trace_id: str, audit: AuditLogger,
    ) -> InvokeResponse:
        """Continue an active workflow (bypass LLM routing)."""
        wf_state = session.workflow_state or {}

        # ── paused_for_review: waiting on a human reviewer ──
        # Exit intent was already handled by the caller before we got here.
        # A "继续"/"resume" message advances past the human_review step
        # (MVP — no external approval API this wave); anything else just
        # gets a canned "already escalated" reply.
        if wf_state.get("status") == "paused_for_review":
            message = (req.message or "").strip().lower()
            if any(kw in message for kw in _WORKFLOW_RESUME_KEYWORDS):
                tool_gw = ToolGateway(self.db, audit)
                executor = WorkflowExecutor(self.db, tool_gw, audit)
                audit.start_timer("workflow")
                result = await executor.resume_after_review(session)
                wf_latency = audit.elapsed_ms("workflow")
                audit.log("workflow_step", workflow_meta={
                    "status": result.status, "message": result.message,
                }, latency_ms=wf_latency)
                return await self._finalize_workflow_turn(session, req, trace_id, audit, result, executor)

            canned = '已转人工处理，处理完成后可回复"继续"恢复流程。'
            await self._save_message(session.id, "user", req.message, trace_id)
            await self._save_message(session.id, "assistant", canned, trace_id)
            await self._save_session(session)
            await audit.flush()
            return InvokeResponse(
                session_id=session.id,
                trace_id=trace_id,
                short_answer=canned,
                workflow_status="paused_for_review",
                escalated=True,
                suggested_followups=["继续"],
            )

        # ── await_retry: last submission failed after retries ──
        # Only a retry-intent message should re-run the webhook; anything
        # else just re-prompts (never re-hits the webhook for an unrelated
        # message).
        if wf_state.get("status") == "await_retry":
            message = (req.message or "").strip().lower()
            if not any(kw in message for kw in _WORKFLOW_RETRY_KEYWORDS):
                reprompt = '提交尚未成功，回复"重试"重新提交，或"取消"放弃。'
                await self._save_message(session.id, "user", req.message, trace_id)
                await self._save_message(session.id, "assistant", reprompt, trace_id)
                await self._save_session(session)
                await audit.flush()
                return InvokeResponse(
                    session_id=session.id,
                    trace_id=trace_id,
                    short_answer=reprompt,
                    workflow_status="await_retry",
                    suggested_followups=["重试", "取消"],
                )
            # Retry intent confirmed — fall through to re-run process_step,
            # which re-executes the (unchanged) current step: the complete
            # step's webhook, reusing the same idempotency key.

        tool_gw = ToolGateway(self.db, audit)
        executor = WorkflowExecutor(self.db, tool_gw, audit)

        audit.start_timer("workflow")
        result = await executor.process_step(session, req.message, req.form_data)
        wf_latency = audit.elapsed_ms("workflow")

        audit.log("workflow_step", workflow_meta={
            "status": result.status,
            "message": result.message,
        }, latency_ms=wf_latency)

        return await self._finalize_workflow_turn(session, req, trace_id, audit, result, executor)

    async def _finalize_workflow_turn(
        self, session: ConversationSession, req: InvokeRequest,
        trace_id: str, audit: AuditLogger, result, executor=None,
    ) -> InvokeResponse:
        """Shared tail for a workflow turn: terminal cleanup, persistence,
        followups. Only "completed"/"cancelled" are terminal — "escalated"
        (paused_for_review / hard tool-call escalation) and "await_retry"
        are handled by the step handlers themselves (see
        WorkflowExecutor._handle_human_review / _handle_tool_call /
        _handle_complete), which is why they are intentionally excluded
        here (W1-T4).
        """
        if result.status in ("completed", "cancelled"):
            session.active_skill_id = None
            # Set to None — SQLAlchemy reliably detects NULL vs dict change
            session.workflow_state = None
            flag_modified(session, "workflow_state")
            # Hygiene: no cross-ticket bleed into a future workflow (W1-T5).
            session.collected_data = {}
            flag_modified(session, "collected_data")

        await self._save_message(session.id, "user", req.message, trace_id)
        await self._save_message(session.id, "assistant", result.message, trace_id)
        await self._save_session(session)
        await audit.flush()
        # Session committed — dispatch any events the executor queued this turn.
        if executor is not None:
            self._pending_events.extend(executor.pending_events)
        self._flush_pending_events()

        followups = []
        if result.status == "waiting_input":
            followups = ["如何填写？", "需要准备什么材料？"]
        elif result.status == "completed":
            followups = ["查看办理结果", "还有其他问题"]
        elif result.status == "escalated":
            followups = ["人工客服工作时间？", "还能自助办理吗？"]

        return InvokeResponse(
            session_id=session.id,
            trace_id=trace_id,
            short_answer=result.message,
            workflow_card=result.card,
            workflow_status=result.status,
            escalated=result.escalated,
            escalation_reason=result.message if result.escalated else None,
            suggested_followups=followups,
        )

    async def _cancel_active_workflow(
        self, agent: Agent, session: ConversationSession,
        req: InvokeRequest, trace_id: str, audit: AuditLogger,
    ) -> InvokeResponse:
        """Cancel the currently active workflow."""
        tool_gw = ToolGateway(self.db, audit)
        executor = WorkflowExecutor(self.db, tool_gw, audit)

        cancel_msg = executor.cancel_workflow(session)
        session.active_skill_id = None

        audit.log("workflow_step", workflow_meta={
            "status": "cancelled",
            "message": cancel_msg,
        })

        await self._save_message(session.id, "user", req.message, trace_id)
        await self._save_message(session.id, "assistant", cancel_msg, trace_id)
        await self._save_session(session)
        await audit.flush()

        return InvokeResponse(
            session_id=session.id,
            trace_id=trace_id,
            short_answer=cancel_msg,
            workflow_status="cancelled",
            suggested_followups=["有什么其他可以帮助你的吗？", "需要重新开始吗？"],
        )

    # ── Skill loading ────────────────────────────────────────

    async def _load_agent_skills(self, agent_id: str) -> list[Skill]:
        """Load all enabled skills bound to an agent, sorted by priority."""
        result = await self.db.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == agent_id,
                AgentSkill.enabled.is_(True),
            )
        )
        bindings = result.scalars().all()
        if not bindings:
            return []

        skill_ids = [b.skill_id for b in bindings]
        result = await self.db.execute(
            select(Skill).where(
                Skill.id.in_(skill_ids),
                Skill.enabled.is_(True),
            )
        )
        skills = list(result.scalars().all())

        override_map = {
            b.skill_id: b.priority_override
            for b in bindings if b.priority_override is not None
        }
        for s in skills:
            if s.id in override_map:
                s.priority = override_map[s.id]

        skills.sort(key=lambda s: s.priority)
        return skills

    # ── Tool helpers ─────────────────────────────────────────

    async def _load_tools_as_functions(
        self, tool_ids: list[str],
    ) -> tuple[list[dict], dict[str, ToolDefinition]]:
        """Load tool definitions from DB and convert to OpenAI function format."""
        if not tool_ids:
            return [], {}

        result = await self.db.execute(
            select(ToolDefinition).where(
                ToolDefinition.id.in_(tool_ids),
                ToolDefinition.enabled.is_(True),
            )
        )
        tools = list(result.scalars().all())

        openai_tools = []
        tool_map: dict[str, ToolDefinition] = {}

        for tool in tools:
            func_name = self._sanitize_function_name(tool.name)
            parameters = tool.input_schema or {
                "type": "object",
                "properties": {},
                "required": [],
            }
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": func_name,
                    "description": tool.description or f"调用 {tool.name} 工具",
                    "parameters": parameters,
                },
            })
            tool_map[func_name] = tool

        return openai_tools, tool_map

    @staticmethod
    def _sanitize_function_name(name: str) -> str:
        """Sanitize name for OpenAI function calling (alphanumeric + underscores).

        Non-ASCII characters (e.g. Chinese) are transliterated to a hash-based
        suffix so that each unique name produces a unique, stable function name.
        """
        # Extract any ASCII portion first
        ascii_part = re.sub(r'[^a-zA-Z0-9_]', '', name)
        if ascii_part:
            sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
            sanitized = re.sub(r'^[0-9]+', '', sanitized)
            sanitized = re.sub(r'_+', '_', sanitized).strip('_')
            return sanitized or "unnamed"
        # Purely non-ASCII name: use a stable hash to create a unique identifier
        import hashlib
        digest = hashlib.md5(name.encode()).hexdigest()[:8]
        return f"tool_{digest}"

    # ── General helpers ──────────────────────────────────────

    async def _load_agent(self, agent_id: str) -> Agent | None:
        result = await self.db.execute(
            select(Agent).where(Agent.id == agent_id, Agent.enabled.is_(True))
        )
        return result.scalar_one_or_none()

    async def _get_or_create_session(
        self, req: InvokeRequest, agent: Agent,
    ) -> ConversationSession:
        new_session_id = req.session_id or str(uuid.uuid4())
        if req.session_id:
            result = await self.db.execute(
                select(ConversationSession).where(
                    ConversationSession.id == req.session_id
                )
            )
            session = result.scalar_one_or_none()
            if session is not None:
                if (
                    session.agent_id == agent.id
                    and session.tenant_id == agent.tenant_id
                    and session.user_id == req.user_id
                ):
                    return session
                # Session id exists but belongs to another agent/tenant/user:
                # never resume it — start a fresh session under a new id.
                # (Delegation always passes session_id=None, so it never
                # reaches this branch; req.user_id defaults to "anonymous"
                # for unauthenticated callers, and two "anonymous" callers
                # resuming the same session is allowed by design.)
                logger.warning(
                    "Session %s does not belong to agent %s / user; creating a new session",
                    req.session_id, agent.id,
                )
                new_session_id = str(uuid.uuid4())

        session = ConversationSession(
            id=new_session_id,
            agent_id=agent.id,
            user_id=req.user_id,
            tenant_id=agent.tenant_id,
        )
        # Propagate delegation context from parent session
        if req.parent_session_id:
            session.parent_session_id = req.parent_session_id
        if req.shared_context:
            session.shared_context = dict(req.shared_context)
        self.db.add(session)
        await self.db.flush()
        return session

    async def _get_history(self, session_id: str, limit: int = 6) -> list[Message]:
        # Secondary sort on `Message.id` breaks ties when two rows share the
        # same `created_at` (e.g. sub-millisecond writes) so this query and
        # `_get_all_history` agree on a single total order — otherwise the
        # boundary between "already summarized" and "recent window" rows
        # could disagree between the two queries.
        result = await self.db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        msgs = list(result.scalars().all())
        msgs.reverse()
        return msgs

    # ── Current-session memory (Wave 4 / Workstream M) ──────────

    async def _get_persisted_summary(
        self, agent: Agent, session: ConversationSession, audit: AuditLogger,
    ) -> str | None:
        """Cheap READ of the rolling summary already persisted on
        `session.context["summary"]` (or None if none exists yet).

        The fold itself (reload full history, re-apply the threshold/window
        gate, one-shot LLM call, persist + advance the `summarized_upto`
        watermark) no longer happens here — it runs off the critical path in
        `server.engine.summary_scheduler.schedule_summary_update`, scheduled
        after this turn's answer is saved (see the call site in
        `_invoke_conversational`, right after `_save_session`). This method
        does no DB scan and no LLM call; it just returns whatever the last
        completed background fold (from a prior turn) already wrote. `agent`
        and `audit` are accepted for call-site compatibility but unused —
        both are still needed by the scheduler, not by this read.
        """
        context = session.context or {}
        return context.get("summary") or None

    async def _llm_rewrite_query(
        self, agent: Agent, message: str, history_messages: list[Message],
        audit: AuditLogger,
    ) -> str:
        """Resolve pronouns/references in `message` against recent history
        via a one-shot LLM call, producing a standalone retrieval query.

        `_needs_query_rewrite` already gates most turns out before this is
        ever called. On any LLM failure this falls back to the string-concat
        heuristic (`_rewrite_query_with_history`) — this only ever affects
        the retrieval query, never the user-visible answer.
        """
        try:
            convo_text = "\n".join(f"{m.role}: {m.content}" for m in history_messages)
            prompt = (
                "Rewrite the LATEST user message into a standalone search "
                "query by resolving pronouns and implicit references (e.g. "
                "\"它\", \"那个\", \"第二个\") using the conversation history. "
                "If the message is already standalone, return it unchanged. "
                "Output ONLY the rewritten query text — no quotes, no "
                "explanation.\n\n"
                f"Conversation history:\n{convo_text}\n\n"
                f"Latest user message: {message}"
            )
            llm = await get_llm_adapter_for_agent(agent, self.db)
            resp = await llm.chat(
                [
                    LLMMessage(
                        role="system",
                        content="Rewrite a context-dependent query into a standalone retrieval query.",
                    ),
                    LLMMessage(role="user", content=prompt),
                ],
                temperature=0.0,
                max_tokens=120,
            )
            rewritten = (resp.content or "").strip()
            if rewritten:
                audit.log("query_rewrite", event_data={
                    "original": message,
                    "rewritten": rewritten,
                    "source": "llm",
                })
                return rewritten
        except Exception as e:
            audit.log("query_rewrite_llm_error", event_data={"error": str(e)})

        fallback = _rewrite_query_with_history(message, history_messages)
        audit.log("query_rewrite", event_data={
            "original": message,
            "rewritten": fallback,
            "source": "fallback",
        })
        return fallback

    def _load_longterm_memory(self, agent: Agent, req: InvokeRequest) -> str | None:
        """Cross-session memory hook.

        Cross-session memory not implemented this wave; wire here when
        needed (e.g. load a persisted long-term profile/summary for this
        user+agent across sessions and return it as a context string).
        Returns None today — a strict no-op at the single call site in
        `_invoke_conversational`.
        """
        return None

    async def _save_message(
        self, session_id: str, role: str, content: str, trace_id: str,
    ):
        msg = Message(
            session_id=session_id, role=role, content=content, trace_id=trace_id,
        )
        self.db.add(msg)
        await self.db.flush()

    async def _save_session(self, session: ConversationSession):
        session.message_count = (session.message_count or 0) + 1
        await self.db.commit()

    def _risk_precheck(self, agent: Agent, message: str) -> str | None:
        risk_config = agent.risk_config or {}
        forbidden_keywords = risk_config.get("forbidden_keywords", [])
        for kw in forbidden_keywords:
            if kw in message:
                return "您的问题涉及敏感内容，无法回答。如有需要请联系人工客服。"
        return None

    def _error_response(self, message: str, req: InvokeRequest) -> InvokeResponse:
        return InvokeResponse(
            session_id=req.session_id or "",
            trace_id=new_trace_id(),
            short_answer=message,
        )

    def _fallback_response(
        self, session_id: str, trace_id: str, error: str,
    ) -> InvokeResponse:
        """Classified error fallback response."""
        error_lower = error.lower()
        if "not configured" in error_lower:
            msg = "尚未配置语言模型，请到「模型配置」页添加一个配置，或通过控制台的首次运行向导完成设置。"
        elif "timeout" in error_lower or "timed out" in error_lower:
            msg = "请求超时，模型处理较慢，请稍后重试。"
        elif "connection" in error_lower or "connect" in error_lower:
            msg = "无法连接到语言模型服务，请检查服务状态后重试。"
        elif "rate" in error_lower or "429" in error_lower:
            msg = "请求频率过高，请稍后重试。"
        else:
            msg = "系统暂时无法响应，请稍后重试或联系人工客服。"

        return InvokeResponse(
            session_id=session_id,
            trace_id=trace_id,
            short_answer=msg,
            suggested_followups=["转人工客服", "稍后重试"],
            metadata={"error_detail": error},
        )
