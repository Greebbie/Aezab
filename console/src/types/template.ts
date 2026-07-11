export interface TemplateSummary {
  id: string;
  name: string;
  description: string;
  category: string;
}

export interface TemplateInstantiateRequest {
  name?: string;
  llm_config_id?: string;
}
