export interface Workflow {
  id: string;
  name: string;
  description: string;
  tenant_id: string;
  version: number;
  config: Record<string, unknown> | null;
  steps: WorkflowStep[];
  created_at: string;
  updated_at: string;
}

export interface WorkflowStep {
  id: string;
  workflow_id: string;
  name: string;
  order: number;
  step_type: StepType;
  prompt_template: string;
  fields: FieldDef[] | null;
  validation_rules: Record<string, unknown> | null;
  tool_id: string | null;
  tool_config: Record<string, unknown> | null;
  on_failure: OnFailureStrategy;
  max_retries: number;
  fallback_step_id: string | null;
  requires_human_confirm: boolean;
  risk_level: string;
  next_step_rules: NextStepRule[] | null;
  created_at: string;
}

export type StepType = 'collect' | 'validate' | 'tool_call' | 'confirm' | 'human_review' | 'complete';
export type OnFailureStrategy = 'retry' | 'skip' | 'rollback' | 'escalate';

export interface FieldDef {
  name: string;
  label: string;
  field_type: string;
  required: boolean;
  validation_rule?: string | null;
  placeholder?: string;
  options?: Array<{ label: string; value: string }> | null;
  file_config?: FileConfig | null;
  llm_validate?: boolean;
  llm_validate_prompt?: string | null;
}

export interface FileConfig {
  max_size_mb?: number;
  allowed_extensions?: string[];
  storage_path?: string;
}

export interface NextStepRule {
  condition: RuleCondition | null;
  goto_step: string;
}

export interface RuleCondition {
  field: string;
  op: 'eq' | 'ne' | 'gt' | 'lt' | 'gte' | 'lte' | 'contains' | 'regex' | 'in' | 'not_in';
  value: unknown;
}

export interface StepCreate {
  name: string;
  order: number;
  step_type?: StepType;
  prompt_template?: string;
  fields?: FieldDef[];
  validation_rules?: Record<string, unknown>;
  tool_id?: string;
  tool_config?: Record<string, unknown>;
  on_failure?: OnFailureStrategy;
  max_retries?: number;
  fallback_step_id?: string | null;
  next_step_rules?: NextStepRule[];
  requires_human_confirm?: boolean;
  risk_level?: string;
}

export interface WorkflowCreate {
  name: string;
  description?: string;
  tenant_id?: string;
  config?: Record<string, unknown>;
  steps?: StepCreate[];
}

export type WorkflowUpdate = Partial<Pick<WorkflowCreate, 'name' | 'description' | 'config'>>;

export interface WorkflowVersion {
  id: string;
  workflow_id: string;
  version: number;
  snapshot?: Record<string, unknown>;
  published_by: string;
  created_at: string;
}
