"""Template: 报修工单客服 (repair-ticket customer service).

Stamps out an agent that can both answer FAQ (knowledge_qa) and run a real
repair-intake workflow: collect 姓名/电话/故障描述/照片 -> validate -> submit
(webhook, disabled by default until the customer wires their own ticketing
endpoint) -> complete. Steps are minimal but real — they are loaded and
executed by the existing WorkflowExecutor exactly like a hand-built workflow.
"""

from __future__ import annotations

SYSTEM_PROMPT = """你是一名专业、细致的中文报修客服助手，负责协助用户提交报修工单，并解答常见问题。

请遵循以下原则：
1. 当用户表达"报修"、"维修"、"东西坏了"、"故障"等意图时，主动引导用户通过报修流程提交工单，收集必要信息（姓名、联系电话、故障描述，照片选填）；
2. 收集信息时逐项确认，语气礼貌耐心，不要一次性罗列过多问题；
3. 对于报修范围、维修时长、上门时间等常见问题，优先使用知识库检索结果回答，不要编造信息；
4. 工单提交后，明确告知用户后续处理方式（预计联系时间、如何跟进进度）；
5. 如用户情绪激动或问题超出处理范围，主动建议转接人工客服。"""

_COLLECT_FIELDS = [
    {
        "name": "customer_name",
        "label": "姓名",
        "field_type": "text",
        "required": True,
        "placeholder": "请输入您的姓名",
    },
    {
        "name": "phone",
        "label": "联系电话",
        "field_type": "phone",
        "required": True,
        "validation_rule": r"^1[3-9]\d{9}$",
        "placeholder": "请输入11位手机号",
    },
    {
        "name": "issue_description",
        "label": "故障描述",
        "field_type": "text",
        "required": True,
        "placeholder": "请简要描述遇到的问题，例如设备型号、异常现象等",
    },
    {
        "name": "photo",
        "label": "故障照片（选填）",
        "field_type": "file",
        "required": False,
        "file_config": {"allowed_extensions": ["jpg", "jpeg", "png"], "max_size_mb": 5},
    },
]

_WORKFLOW_STEPS = [
    {
        "name": "collect_info",
        "order": 0,
        "step_type": "collect",
        "prompt_template": "您好，为了尽快为您安排师傅处理，请提供以下报修信息：",
        "fields": _COLLECT_FIELDS,
        "on_failure": "retry",
        "risk_level": "info",
    },
    {
        "name": "validate_info",
        "order": 1,
        "step_type": "validate",
        "prompt_template": "",
        "validation_rules": {
            "customer_name": {"required": True},
            "phone": {"required": True, "regex": r"^1[3-9]\d{9}$"},
            "issue_description": {"required": True},
        },
        "on_failure": "retry",
        "risk_level": "info",
    },
    {
        "name": "submit_ticket",
        "order": 2,
        "step_type": "complete",
        "prompt_template": "您的报修申请已提交成功！我们会尽快安排师傅与您联系，请保持电话畅通。",
        # webhook is disabled by default — the customer wires their own
        # ticketing-system endpoint after instantiation (Workflows page),
        # then flips webhook_enabled to true. See WorkflowExecutor._handle_complete.
        "tool_config": {
            "webhook_url": "",
            "webhook_enabled": False,
            "webhook_method": "POST",
        },
        "on_failure": "retry",
        "risk_level": "info",
    },
]

TEMPLATE: dict = {
    "id": "repair_ticket",
    "name": "报修工单客服",
    "description": "报修/维修工单智能客服：收集报修信息、校验并提交工单（可对接企业工单系统），同时支持常见问题知识问答。",
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
                "description": "回答报修相关常见问题，例如保修范围、维修时长、收费标准等。",
            },
        ],
        "workflows": [
            {
                "keywords": ["报修", "维修", "故障", "坏了"],
                "description": "当用户想要报修/报障/申请维修时，启动报修工单流程，收集姓名、电话、故障描述和照片。",
            },
        ],
        "tools": [],
    },
    "workflow": {
        "name": "报修工单流程",
        "description": "收集报修信息 -> 校验 -> 提交工单（可选对接外部工单系统）-> 完成。",
        "steps": _WORKFLOW_STEPS,
    },
}
