export interface BusinessField {
  name: string;
  type: 'string' | 'integer' | 'number' | 'boolean' | 'date';
  required?: boolean;
}

export interface BusinessSourceInput {
  name: string;
  description?: string;
  kind: 'table' | 'postgres';
  fields: BusinessField[];
  filter_fields: string[];
  required_filters: string[];
  max_rows: number;
  enabled: boolean;
  postgres_dsn?: string;
  postgres_schema?: string;
  postgres_table?: string;
  tenant_column?: string | null;
}

export interface BusinessSource extends Omit<BusinessSourceInput, 'postgres_dsn'> {
  id: string;
  tool_id: string;
  tool_name?: string;
  tenant_id: string;
  has_credentials: boolean;
  created_at: string;
  updated_at: string;
}

export type BusinessValues = Record<string, string | number | boolean | null>;
export interface BusinessRecord {
  id: string;
  values: BusinessValues;
  created_at: string;
  updated_at: string;
}

export interface BusinessRecordPage {
  items: BusinessRecord[];
  total: number;
  offset: number;
  limit: number;
}

export interface BusinessQueryResult {
  source_id: string;
  columns: BusinessField[];
  rows: BusinessValues[];
  row_count: number;
  truncated: boolean;
}
