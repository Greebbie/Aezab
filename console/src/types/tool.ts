export interface Tool {
  id: string;
  name: string;
  description: string;
  category: 'api' | 'function' | 'webhook' | 'rpc' | 'data_query';
  endpoint: string;
  method: string;
  input_schema: Record<string, unknown> | null;
  output_schema: Record<string, unknown> | null;
  auth_config: { type?: string; header?: string; has_token?: boolean } | null;
  timeout_ms: number;
  max_retries: number;
  retry_backoff_ms: number;
  is_async: boolean;
  risk_level: string;
  tenant_id: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ToolCreate {
  name: string;
  description?: string;
  category?: string;
  endpoint?: string;
  method?: string;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  auth_config?: Record<string, unknown>;
  timeout_ms?: number;
  max_retries?: number;
  risk_level?: string;
  tenant_id?: string;
}

export type ToolUpdate = Partial<ToolCreate>;
