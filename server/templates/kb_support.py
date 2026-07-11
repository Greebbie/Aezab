"""Template: 知识问答客服 (knowledge-QA customer service).

Dead-simple starting point for a customer who just wants to upload their
product/service docs and get a polite, refusal-safe Chinese Q&A agent —
no workflow, just a knowledge_qa skill wired up automatically.
"""

from __future__ import annotations

SYSTEM_PROMPT = """你是一名专业、耐心的中文智能客服助手，负责基于企业知识库回答用户的咨询问题。

请遵循以下原则：
1. 使用礼貌、简洁、专业的中文与用户沟通，避免生硬或机械的语气；
2. 只根据知识库中检索到的内容回答问题，绝不编造或猜测信息；
3. 如果知识库中没有找到相关信息，请诚实告知用户"暂未查询到相关信息"，并建议用户换一种问法或联系人工客服，不要编造答案；
4. 回答尽量简洁明了，信息较多时可分点说明，方便用户快速理解；
5. 遇到用户情绪激动、投诉或超出知识库范围的复杂问题时，主动提示可以转接人工客服处理；
6. 每次回答后，如有必要可以给出后续追问建议，帮助用户进一步了解相关信息。"""

TEMPLATE: dict = {
    "id": "kb_support",
    "name": "知识问答客服",
    "description": "基于知识库的智能问答客服，适合被频繁咨询产品/服务信息的场景。上传文档后即可直接使用，无需手动配置技能。",
    "category": "customer_service",
    "system_prompt": SYSTEM_PROMPT,
    "response_config": {
        "default_mode": "short",
        "enable_citations": True,
        "enable_followups": True,
        "max_short_tokens": 150,
        "no_citation_policy": "refuse",
    },
    "risk_config": {
        "forbidden_keywords": [],
    },
    "capabilities": {
        "knowledge": [
            {
                "domain": "default",
                "source_ids": [],
                "keywords": [],
                "description": "搜索企业知识库，回答用户关于产品、服务、政策等方面的咨询问题。",
            },
        ],
        "workflows": [],
        "tools": [],
    },
    "workflow": None,
}
