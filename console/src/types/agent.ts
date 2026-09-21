export interface Agent {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  llm_model: string | null;
  llm_config_id: string | null;
  response_config: ResponseConfig | null;
  risk_config: Record<string, unknown> | null;
  skill_routing_mode: string;
  tenant_id: string;
  enabled: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ResponseConfig {
  default_mode: 'short' | 'expanded';
  enable_citations: boolean;
  enable_followups: boolean;
  max_short_tokens: number;
  no_citation_policy: 'refuse' | 'escalate' | 'create_ticket';
}

export interface AgentCreate {
  name: string;
  description?: string;
  system_prompt?: string;
  llm_model?: string;
  llm_config_id?: string | null;
  response_config?: Partial<ResponseConfig>;
  risk_config?: Record<string, unknown>;
  skill_routing_mode?: string;
  tenant_id?: string;
  enabled?: boolean;
}

export type AgentUpdate = Partial<AgentCreate>;

export interface AgentCapabilities {
  knowledge: KnowledgeCapability[];
  workflows: WorkflowCapability[];
  tools: ToolCapability[];
}

export interface KnowledgeCapability {
  domain: string;
  source_ids: string[];
  keywords: string[];
  description: string;
}

export interface WorkflowCapability {
  workflow_id: string;
  keywords: string[];
  description: string;
}

export interface ToolCapability {
  tool_ids: string[];
  keywords: string[];
  description: string;
}

export interface AgentConnection {
  id: string;
  source_agent_id: string;
  target_agent_id: string;
  connection_type: string;
  shared_context: Record<string, unknown> | null;
  description: string;
  enabled: boolean;
  tenant_id: string;
  created_at: string;
}

export interface AgentConnectionCreate {
  source_agent_id: string;
  target_agent_id: string;
  connection_type: string;
  shared_context?: Record<string, unknown>;
  description?: string;
  enabled?: boolean;
  tenant_id?: string;
}
