"""Template: 预约办理客服 (appointment/booking customer service).

Stamps out an agent that runs a real booking workflow: collect
姓名/电话/事项/期望时间 -> confirm -> submit (webhook, disabled by default) ->
complete, plus knowledge QA for FAQ. Steps are minimal but real — loaded and
executed by the existing WorkflowExecutor.
"""

from __future__ import annotations

SYSTEM_PROMPT = """你是一名专业、周到的中文预约客服助手，负责协助用户完成预约/办理类事项，并解答常见问题。

请遵循以下原则：
1. 当用户表达"预约"、"办理"、"想约个时间"等意图时，主动引导用户通过预约流程提交申请，收集必要信息（姓名、联系电话、预约事项、期望时间）；
2. 收集信息时逐项确认，语气礼貌耐心；
3. 在用户确认提交前，完整复述已收集的信息，请用户核实无误后再提交；
4. 对于营业时间、可预约范围、取消/改期政策等常见问题，优先使用知识库检索结果回答，不要编造信息；
5. 预约提交后，明确告知用户后续联系方式和注意事项；
6. 如用户情绪激动或问题超出处理范围，主动建议转接人工客服。"""

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
        "name": "matter",
        "label": "预约事项",
        "field_type": "text",
        "required": True,
        "placeholder": "请说明您要办理的事项",
    },
    {
        "name": "appointment_time",
        "label": "期望时间",
        "field_type": "text",
        "required": True,
        "placeholder": "例如：明天下午3点",
    },
]

_WORKFLOW_STEPS = [
    {
        "name": "collect_info",
        "order": 0,
        "step_type": "collect",
        "prompt_template": "您好，请提供以下预约信息，方便我们为您安排：",
        "fields": _COLLECT_FIELDS,
        "on_failure": "retry",
        "risk_level": "info",
    },
    {
        "name": "confirm_info",
        "order": 1,
        "step_type": "confirm",
        "prompt_template": '请核对以上预约信息是否正确？回复"确认"提交预约，或回复"取消"重新填写。',
        "requires_human_confirm": True,
        "on_failure": "retry",
        "risk_level": "info",
    },
    {
        "name": "submit_booking",
        "order": 2,
        "step_type": "complete",
        "prompt_template": "您的预约申请已提交成功！我们会尽快与您联系确认具体时间。",
        # webhook is disabled by default — the customer wires their own
        # booking-system endpoint after instantiation (Workflows page),
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
    "id": "booking",
    "name": "预约办理客服",
    "description": "预约/办理类智能客服：收集预约信息、确认后提交（可对接企业预约系统），同时支持常见问题知识问答。",
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
                "description": "回答预约相关常见问题，例如营业时间、可预约范围、取消/改期政策等。",
            },
        ],
        "workflows": [
            {
                "keywords": ["预约", "办理", "约个时间"],
                "description": "当用户想要预约/办理某项事务时，启动预约流程，收集姓名、电话、事项和期望时间。",
            },
        ],
        "tools": [],
    },
    "workflow": {
        "name": "预约办理流程",
        "description": "收集预约信息 -> 确认 -> 提交（可选对接外部预约系统）-> 完成。",
        "steps": _WORKFLOW_STEPS,
    },
}
