import type { NextStepRule, NextStepRules, WorkflowStep } from '../../types';

export function workflowRules(value: NextStepRules | null | undefined): NextStepRule[] {
  return Array.isArray(value) ? value : value?.rules || [];
}

export function resolveStep(steps: WorkflowStep[], target: string): WorkflowStep | undefined {
  return steps.find((step) => step.id === target)
    || steps.find((step) => step.name === target)
    || steps.find((step) => String(step.order) === String(target));
}

export function editableRules(steps: WorkflowStep[], value: NextStepRules | null | undefined): NextStepRule[] {
  return workflowRules(value).map((rule) => ({
    ...rule,
    goto_step: resolveStep(steps, rule.goto_step)?.id || rule.goto_step,
    condition: rule.condition ? {
      ...rule.condition,
      value: ['in', 'not_in'].includes(rule.condition.op) && typeof rule.condition.value === 'string'
        ? rule.condition.value.split(',').map((item) => item.trim()) : rule.condition.value,
    } : null,
  }));
}

export type WorkflowRoute = {
  id: string;
  source: string;
  target: string;
  kind: 'condition' | 'default' | 'implicit' | 'failure';
  ruleIndex?: number;
  rule?: NextStepRule;
};

export function workflowRoutes(steps: WorkflowStep[]): WorkflowRoute[] {
  const ordered = [...steps].sort((a, b) => a.order - b.order);
  return ordered.flatMap((step, index) => {
    if (step.step_type === 'complete') return [];
    const routes: WorkflowRoute[] = [];
    const rules = workflowRules(step.next_step_rules);
    rules.forEach((rule, ruleIndex) => {
      const target = resolveStep(ordered, rule.goto_step);
      if (target) routes.push({
        id: `${step.id}:rule:${ruleIndex}`, source: step.id, target: target.id,
        kind: rule.condition ? 'condition' : 'default', ruleIndex, rule,
      });
    });
    if (!rules.some((rule) => !rule.condition) && ordered[index + 1]) routes.push({
      id: `${step.id}:implicit`, source: step.id, target: ordered[index + 1].id, kind: 'implicit',
    });
    if (step.fallback_step_id) {
      const target = resolveStep(ordered, step.fallback_step_id);
      if (target) routes.push({
        id: `${step.id}:failure`, source: step.id, target: target.id, kind: 'failure',
      });
    }
    return routes;
  });
}
