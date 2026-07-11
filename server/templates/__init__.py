"""Built-in agent templates — the 傻瓜式 (dead-simple) agent-stamping catalog.

Pure data: each template describes an Agent (system_prompt, response_config,
risk_config), the skills that should be auto-created for it (mirroring the
capabilities spec shape consumed by server/api/agent_capabilities.py), and
an optional starter workflow (name/description/steps) for templates that
need one. See server/api/agent_templates.py for the instantiate endpoint
that turns this data into real Agent/Workflow/WorkflowStep/Skill rows.
"""

from __future__ import annotations

from server.templates.booking import TEMPLATE as BOOKING_TEMPLATE
from server.templates.kb_support import TEMPLATE as KB_SUPPORT_TEMPLATE
from server.templates.repair_ticket import TEMPLATE as REPAIR_TICKET_TEMPLATE

_TEMPLATES: list[dict] = [
    KB_SUPPORT_TEMPLATE,
    REPAIR_TICKET_TEMPLATE,
    BOOKING_TEMPLATE,
]

_TEMPLATES_BY_ID: dict[str, dict] = {t["id"]: t for t in _TEMPLATES}


def list_templates() -> list[dict]:
    """Return all built-in templates (pure data, tenant-agnostic)."""
    return list(_TEMPLATES)


def get_template(template_id: str) -> dict | None:
    """Look up a single built-in template by id, or None if unknown."""
    return _TEMPLATES_BY_ID.get(template_id)
