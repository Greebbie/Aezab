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
from server.schemas.invoke import (
    InvokeRequest,
    InvokeResponse,
    Citation,
    WorkflowCard,
)
from server.engine.llm_adapter import LLMMessage, get_llm_adapter_for_agent
from server.engine.knowledge_retriever import KnowledgeRetriever
from server.engine.tool_gateway import ToolGateway
from server.engine.workflow_executor import WorkflowExecutor
from server.engine.audit_logger import AuditLogger, new_trace_id
from server.engine.vector_store import get_vector_store_if_initialized
from server.runtime_config import runtime_config

logger = logging.getLogger(__name__)

# Maximum tool-calling rounds before forcing a final answer
MAX_TOOL_ROUNDS = 5

# Maximum delegation depth
MAX_DELEGATION_DEPTH = 3


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


# ── Workflow exit keywords ─────────────────────────────────────

_WORKFLOW_EXIT_KEYWORDS = {
    "取消", "退出", "不办了", "算了", "不要了", "放弃",
    "cancel", "quit", "exit", "abort", "stop",
}

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


class AgentRuntime:
    """Main orchestrator for handling user requests.

    Conversation-first architecture: every message goes to the LLM,
    which decides whether to respond directly or call skill-tools.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Main invoke entry point ──────────────────────────────

    async def invoke(self, req: InvokeRequest) -> InvokeResponse:
        """Full pipeline: one user message in, one structured response out."""

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

        # 4. Risk pre-check
        risk_block = self._risk_precheck(agent, req.message)
        if risk_block:
            audit.log("risk_block", event_data={"reason": risk_block})
            await audit.flush()
            return InvokeResponse(
                session_id=session.id,
                trace_id=trace_id,
                short_answer=risk_block,
                suggested_followups=["换个问题试试？"],
            )

        # 5. Conversational pipeline
        return await self._invoke_conversational(agent, session, req, trace_id, audit)

    # ── Conversational invocation ─────────────────────────────

    async def _invoke_conversational(
        self, agent: Agent, session: ConversationSession,
        req: InvokeRequest, trace_id: str, audit: AuditLogger,
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
        if wf_state.get("status") in ("in_progress", "waiting_input"):
            # Check for workflow exit keywords
            for kw in _WORKFLOW_EXIT_KEYWORDS:
                if kw in msg_lower:
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
                history = await self._get_history(session.id, limit=6)
                retrieval_query = req.message
                if _needs_query_rewrite(req.message) and history:
                    retrieval_query = _rewrite_query_with_history(req.message, history)
                    audit.log("query_rewrite", event_data={
                        "original": req.message,
                        "rewritten": retrieval_query,
                    })
                pre_context, pre_citations = await self._pre_retrieve(
                    retrieval_query, knowledge_skills, audit,
                )

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
        history = await self._get_history(session.id, limit=6)

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

        for round_idx in range(MAX_TOOL_ROUNDS):
            audit.start_timer(f"llm_round_{round_idx}")

            try:
                if tool_defs:
                    llm_resp = await llm.chat_with_tools(messages, tool_defs)
                else:
                    llm_resp = await llm.chat(messages)
            except Exception as e:
                audit.log("error", event_data={
                    "error": str(e), "round": round_idx, "stage": "llm_call",
                })
                if action_intent.detected:
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
            }, latency_ms=round_latency)

            # No tool calls → final answer
            if not llm_resp.tool_calls:
                if action_intent.detected:
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
                final_content = llm_resp.content
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

                audit.start_timer(f"tool_{tc.id}")
                try:
                    result: SkillToolResult = await handler(tc.arguments)
                except Exception as e:
                    logger.warning(f"Skill tool handler error [{tc.function_name}]: {e}")
                    result = SkillToolResult(text=f"Error: {e}")

                tool_latency = audit.elapsed_ms(f"tool_{tc.id}")
                audit.log("tool_call", tool_meta={
                    "function_name": tc.function_name,
                    "arguments": tc.arguments,
                    "result_preview": result.text[:200],
                    "round": round_idx,
                }, latency_ms=tool_latency)

                messages.append(LLMMessage(
                    role="tool",
                    content=result.text,
                    tool_call_id=tc.id,
                ))

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

        # Safety net: refusal override when retrieval found data
        if collected_citations and any(p in short_answer for p in _REFUSAL_PHRASES):
            for c in collected_citations:
                if c.content_snippet:
                    short_answer = c.content_snippet
                    break

        # Save messages
        await self._save_message(session.id, "user", req.message, trace_id)
        await self._save_message(session.id, "assistant", short_answer, trace_id)
        await self._save_session(session)

        # Followups
        if collected_workflow_card:
            followups = ["如何填写？", "需要准备什么材料？"]
        elif collected_citations:
            followups = ["需要更多详细信息吗？", "还有其他问题吗？"]
        elif tool_calls_log:
            followups = ["查看详细结果", "还有其他问题吗？"]
        else:
            followups = ["有什么可以帮助你的吗？"]

        audit.log("response", event_data={
            "short_answer": short_answer[:200],
            "mode": "conversational",
            "tool_calls_count": len(tool_calls_log),
            "citations_count": len(collected_citations),
            "fallback": fallback_info,
        })
        await audit.flush()

        metadata = {"mode": "conversational"}
        if tool_calls_log:
            metadata["tool_calls"] = tool_calls_log
        if fallback_info:
            metadata["fallback"] = fallback_info

        return InvokeResponse(
            session_id=session.id,
            trace_id=trace_id,
            short_answer=short_answer,
            citations=collected_citations,
            suggested_followups=followups,
            workflow_card=collected_workflow_card,
            workflow_status=collected_workflow_status,
            skill_info=collected_skill_info[0] if collected_skill_info else None,
            metadata=metadata if len(metadata) > 1 else None,
        )

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
                    pre_retrieved=pre_retrieved,
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
        pre_retrieved: bool = False,
    ) -> None:
        domain = config.get("domain", "default")
        name = unique_name(
            f"search_knowledge_{domain}" if domain != "default" else "search_knowledge"
        )
        base_desc = skill.description or f"搜索知识库 ({domain}) 获取相关信息"
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
        handler_map[name] = self._make_knowledge_handler(skill, config)

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
            if fn_name != orig_fn_name:
                tool_def = {
                    **tool_def,
                    "function": {**tool_def["function"], "name": fn_name},
                }
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
        base_desc = skill.description or f"启动业务流程: {skill.name}"
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
                    sub_skill, sub_config, tool_defs, handler_map, unique_name,
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
        self, skill: Skill, config: dict,
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

            if not retrieval or not retrieval.hits:
                return SkillToolResult(text="未找到相关信息。")

            parts = []
            citations = []
            for i, hit in enumerate(retrieval.hits[:5]):
                parts.append(f"[{i+1}] {hit.content}")
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
            }
            flag_modified(session, "workflow_state")
            session.active_skill_id = skill.id

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
            }, latency_ms=pre_latency)

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

    async def _continue_active_workflow(
        self, agent: Agent, session: ConversationSession,
        req: InvokeRequest, trace_id: str, audit: AuditLogger,
    ) -> InvokeResponse:
        """Continue an active workflow (bypass LLM routing)."""
        tool_gw = ToolGateway(self.db, audit)
        executor = WorkflowExecutor(self.db, tool_gw, audit)

        audit.start_timer("workflow")
        result = await executor.process_step(session, req.message, req.form_data)
        wf_latency = audit.elapsed_ms("workflow")

        audit.log("workflow_step", workflow_meta={
            "status": result.status,
            "message": result.message,
        }, latency_ms=wf_latency)

        if result.status in ("completed", "cancelled", "escalated"):
            session.active_skill_id = None
            # Set to None — SQLAlchemy reliably detects NULL vs dict change
            session.workflow_state = None
            flag_modified(session, "workflow_state")

        await self._save_message(session.id, "user", req.message, trace_id)
        await self._save_message(session.id, "assistant", result.message, trace_id)
        await self._save_session(session)
        await audit.flush()

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
        if req.session_id:
            result = await self.db.execute(
                select(ConversationSession).where(
                    ConversationSession.id == req.session_id
                )
            )
            session = result.scalar_one_or_none()
            if session:
                return session

        session = ConversationSession(
            id=req.session_id or str(uuid.uuid4()),
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
        result = await self.db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        msgs = list(result.scalars().all())
        msgs.reverse()
        return msgs

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
        if "timeout" in error_lower or "timed out" in error_lower:
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
