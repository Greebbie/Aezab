"""Language detection + bilingual server-generated text.

The engine emits a number of server-generated, user-facing strings that no
frontend i18n layer can translate: fallback answers, suggested followups,
classified error messages, workflow re-prompts. This module keys them by
language so an English conversation never receives hardcoded Chinese.

Detection is deliberately simple: any CJK ideograph in the user's message
means "zh", otherwise "en". The language is a per-request concern — callers
detect once from the incoming message and pass ``lang`` down.
"""

from __future__ import annotations

import re
from typing import Literal

Lang = Literal["zh", "en"]

_CJK_RE = re.compile(r"[一-鿿]")


def detect_lang(*texts: str | None) -> Lang:
    """Return "zh" if any given text contains CJK ideographs, else "en"."""
    for text in texts:
        if text and _CJK_RE.search(text):
            return "zh"
    return "en"


_STRINGS: dict[str, dict[Lang, str]] = {
    "agent_not_found": {
        "zh": "Agent 不存在或已停用",
        "en": "This agent doesn't exist or has been disabled.",
    },
    "risk_blocked": {
        "zh": "您的问题涉及敏感内容，无法回答。如有需要请联系人工客服。",
        "en": "This question involves restricted content and can't be answered. Please contact support if you need help.",
    },
    "default_persona": {
        "zh": "你是一个智能助手。",
        "en": "You are a helpful assistant.",
    },
    "expand_nudge": {
        "zh": "请给出详细完整的回答。",
        "en": "Please give a detailed and complete answer.",
    },
    "summary_prefix": {
        "zh": "[对话摘要]",
        "en": "[Conversation summary]",
    },
    "action_intent_nudge": {
        "zh": "[系统：检测到{kind}请求，请调用最匹配的工具处理，不要用文字直接代办。]",
        "en": "[System: a {kind} request was detected. Call the best-matching tool instead of answering in plain text.]",
    },
    "op_done": {
        "zh": "操作已完成。",
        "en": "Done — the requested action has been completed.",
    },
    "empty_reply": {
        "zh": "抱歉，未能获取到有效回复，请重试。",
        "en": "Sorry, I couldn't generate a reply. Please try again.",
    },
    "refusal_supplement_intro": {
        "zh": "以下是检索到的可能相关的资料片段，供参考：",
        "en": "The following retrieved excerpts may be relevant:",
    },
    "kb_search_failed": {
        "zh": "知识库检索失败，请稍后重试。",
        "en": "Knowledge search failed. Please try again later.",
    },
    "kb_no_results": {
        "zh": "未找到相关信息。",
        "en": "No relevant information found.",
    },
    "source_n": {
        "zh": "来源{n}",
        "en": "Source {n}",
    },
    "tool_call_failed": {
        "zh": "工具调用失败: {e}",
        "en": "Tool call failed: {e}",
    },
    "wf_start_failed": {
        "zh": "流程启动失败: {e}",
        "en": "Failed to start the workflow: {e}",
    },
    "delegate_cycle": {
        "zh": "检测到循环委派，无法继续。",
        "en": "Delegation cycle detected — cannot continue.",
    },
    "delegate_depth": {
        "zh": "委派深度超过限制 ({n})。",
        "en": "Delegation depth limit exceeded ({n}).",
    },
    "delegate_failed": {
        "zh": "委派失败: {e}",
        "en": "Delegation failed: {e}",
    },
    "wf_escalated_canned": {
        "zh": '已转人工处理，处理完成后可回复"继续"恢复流程。',
        "en": 'This has been escalated to a human agent. Reply "resume" to continue once it has been handled.',
    },
    "wf_retry_reprompt": {
        "zh": '提交尚未成功，回复"重试"重新提交，或"取消"放弃。',
        "en": 'The submission hasn\'t gone through yet. Reply "retry" to submit again, or "cancel" to give up.',
    },
    "err_not_configured": {
        "zh": "尚未配置语言模型，请到「模型配置」页添加一个配置，或通过控制台的首次运行向导完成设置。",
        "en": "No language model is configured yet. Add one on the Model Configs page, or run the first-time setup wizard in the console.",
    },
    "err_timeout": {
        "zh": "请求超时，模型处理较慢，请稍后重试。",
        "en": "The request timed out — the model is responding slowly. Please try again later.",
    },
    "err_connection": {
        "zh": "无法连接到语言模型服务，请检查服务状态后重试。",
        "en": "Couldn't reach the language-model service. Please check the service status and try again.",
    },
    "err_rate_limited": {
        "zh": "请求频率过高，请稍后重试。",
        "en": "Too many requests. Please try again shortly.",
    },
    "err_generic": {
        "zh": "系统暂时无法响应，请稍后重试或联系人工客服。",
        "en": "The system is temporarily unavailable. Please try again later or contact support.",
    },
    "err_pipeline_timeout": {
        "zh": "请求处理超时，请稍后重试。",
        "en": "The request took too long to process. Please try again later.",
    },
    "err_llm": {
        "zh": "语言模型服务异常，请检查配置后重试。",
        "en": "The language-model service returned an error. Please check the configuration and try again.",
    },
    "err_retrieval": {
        "zh": "知识检索服务异常，请稍后重试。",
        "en": "The knowledge-retrieval service failed. Please try again later.",
    },
    "err_workflow": {
        "zh": "流程执行异常，请检查流程配置。",
        "en": "Workflow execution failed. Please check the workflow configuration.",
    },
    "err_tool": {
        "zh": "工具调用失败，请检查工具配置。",
        "en": "The tool call failed. Please check the tool configuration.",
    },
}

_FOLLOWUPS: dict[str, dict[Lang, list[str]]] = {
    "fu_try_different": {
        "zh": ["换个问题试试？"],
        "en": ["Try asking something else"],
    },
    "fu_workflow_filling": {
        "zh": ["如何填写？", "需要准备什么材料？"],
        "en": ["How do I fill this in?", "What information is required?"],
    },
    "fu_more_detail": {
        "zh": ["需要更多详细信息吗？", "还有其他问题吗？"],
        "en": ["Show me more detail", "I have another question"],
    },
    "fu_tool_result": {
        "zh": ["查看详细结果", "还有其他问题吗？"],
        "en": ["Show the full result", "I have another question"],
    },
    "fu_anything_else": {
        "zh": ["有什么可以帮助你的吗？"],
        "en": ["What can you help me with?"],
    },
    "fu_resume": {
        "zh": ["继续"],
        "en": ["resume"],
    },
    "fu_retry_cancel": {
        "zh": ["重试", "取消"],
        "en": ["retry", "cancel"],
    },
    "fu_wf_completed": {
        "zh": ["查看办理结果", "还有其他问题"],
        "en": ["View the result", "I have another question"],
    },
    "fu_wf_escalated": {
        "zh": ["人工客服工作时间？", "还能自助办理吗？"],
        "en": ["When is human support available?", "Can I still do this myself?"],
    },
    "fu_wf_cancelled": {
        "zh": ["有什么其他可以帮助你的吗？", "需要重新开始吗？"],
        "en": ["Is there anything else I can help with?", "Start over?"],
    },
    "fu_error": {
        "zh": ["转人工客服", "稍后重试"],
        "en": ["Contact support", "Try again later"],
    },
}


def server_text(key: str, lang: Lang) -> str:
    """Return the server-generated string for ``key`` in ``lang``."""
    entry = _STRINGS[key]
    return entry["zh"] if lang == "zh" else entry["en"]


def server_followups(key: str, lang: Lang) -> list[str]:
    """Return a fresh copy of the suggested-followup list for ``key``."""
    entry = _FOLLOWUPS[key]
    return list(entry["zh"] if lang == "zh" else entry["en"])
