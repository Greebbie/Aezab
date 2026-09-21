"""Rule evaluator for workflow conditional branching.

Evaluates next_step_rules to determine which step to jump to
based on collected data from previous workflow steps.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def evaluate_rules(
    rules: list[dict[str, Any]],
    collected_data: dict[str, Any],
) -> str | None:
    """Evaluate a list of conditional rules against collected data.

    Each rule has the form:
        {
            "condition": {"field": "user_type", "op": "eq", "value": "enterprise"} | null,
            "goto_step": "step_name_or_order"
        }

    A rule with condition=null is the default (fallthrough).
    Returns the goto_step of the first matching rule, or None if no rules match.
    """
    if not rules:
        return None

    for rule in rules:
        condition = rule.get("condition")
        goto_step = rule.get("goto_step")

        if condition is None:
            # Default fallthrough rule
            logger.debug("Rule default fallthrough -> %s", goto_step)
            return goto_step

        if _evaluate_condition(condition, collected_data):
            logger.debug("Rule matched: %s -> %s", condition, goto_step)
            return goto_step

    return None


def _evaluate_condition(condition: dict[str, Any], data: dict[str, Any]) -> bool:
    """Evaluate a single condition against collected data."""
    field = condition.get("field", "")
    op = condition.get("op", "eq")
    expected = condition.get("value")

    actual = data.get(field)

    # Handle None/missing field
    if actual is None:
        return op in ("eq", "is") and expected is None

    try:
        return _OPERATORS[op](actual, expected)
    except (KeyError, TypeError, ValueError, re.error) as e:
        logger.warning("Rule evaluation error: op=%s, field=%s, error=%s", op, field, e)
        return False


def _op_eq(actual: Any, expected: Any) -> bool:
    return str(actual).strip() == str(expected).strip()

def _op_ne(actual: Any, expected: Any) -> bool:
    return str(actual).strip() != str(expected).strip()

def _op_gt(actual: Any, expected: Any) -> bool:
    return float(actual) > float(expected)

def _op_lt(actual: Any, expected: Any) -> bool:
    return float(actual) < float(expected)

def _op_gte(actual: Any, expected: Any) -> bool:
    return float(actual) >= float(expected)

def _op_lte(actual: Any, expected: Any) -> bool:
    return float(actual) <= float(expected)

def _op_contains(actual: Any, expected: Any) -> bool:
    return str(expected) in str(actual)

def _op_not_contains(actual: Any, expected: Any) -> bool:
    return str(expected) not in str(actual)

def _op_regex(actual: Any, expected: Any) -> bool:
    return bool(re.search(str(expected), str(actual)))

def _op_in(actual: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return str(actual).strip() in [str(v).strip() for v in expected]
    return str(actual).strip() in str(expected).split(",")

def _op_not_in(actual: Any, expected: Any) -> bool:
    return not _op_in(actual, expected)


_OPERATORS = {
    "eq": _op_eq,
    "ne": _op_ne,
    "gt": _op_gt,
    "lt": _op_lt,
    "gte": _op_gte,
    "lte": _op_lte,
    "contains": _op_contains,
    "not_contains": _op_not_contains,
    "regex": _op_regex,
    "in": _op_in,
    "not_in": _op_not_in,
}
