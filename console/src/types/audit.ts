export interface AuditTrace {
  id: string;
  trace_id: string;
  session_id: string;
  agent_id: string;
  tenant_id: string;
  event_type: string;
  event_data: Record<string, unknown> | null;
  retrieval_hits: Record<string, unknown> | null;
  llm_meta: Record<string, unknown> | null;
  tool_meta: Record<string, unknown> | null;
  workflow_meta: Record<string, unknown> | null;
  escalation_reason?: string;
  latency_ms?: number;
  timestamp?: string;
}

export interface AuditTraceSummary {
  id: string;
  trace_id: string;
  session_id: string;
  agent_id: string;
  event_type: string;
  latency_ms: number | null;
  timestamp: string | null;
}

export interface AuditTraceListResponse {
  total: number;
  offset: number;
  limit: number;
  items: AuditTraceSummary[];
}

export interface AuditMetrics {
  total_requests: number;
  avg_latency_ms: number;
  error_rate: number;
  escalation_rate: number;
  top_agents: Array<{ agent_id: string; count: number }>;
}

// GET /audit/usage — business-facing token usage summary (see
// server/api/audit.py::get_usage). Aggregated from `llm_call` audit
// events; rows predating token tracking simply contribute 0 tokens.
export interface UsageByDay {
  date: string;
  invocations: number;
  tokens: number;
}

export interface UsageByAgent {
  agent_id: string;
  agent_name: string;
  invocations: number;
  tokens: number;
}

export interface UsageSummary {
  period_days: number;
  total_invocations: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  by_day: UsageByDay[];
  by_agent: UsageByAgent[];
}
