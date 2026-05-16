import axios, { AxiosInstance, AxiosResponse } from 'axios';
import type {
  Agent, AgentCreate, AgentUpdate, AgentCapabilities, AgentConnection, AgentConnectionCreate,
  Workflow, WorkflowCreate, WorkflowUpdate, StepCreate, WorkflowVersion,
  KnowledgeSource, KnowledgeSourceCreate, RetrievalResponse,
  Tool, ToolCreate, ToolUpdate,
  Skill, SkillCreate, SkillUpdate,
  LLMConfig, LLMConfigCreate, LLMConfigUpdate, LLMTemplate,
  AuditTrace, AuditTraceListResponse, AuditMetrics,
  InvokeRequest, InvokeResponse,
} from './types';

const api: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

// Global error interceptor
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const message = error.response?.data?.detail || error.message || 'Unknown error';
    return Promise.reject(new Error(message));
  },
);

// Helper to extract data from AxiosResponse
function unwrap<T>(promise: Promise<AxiosResponse<T>>): Promise<T> {
  return promise.then((res) => res.data);
}

// ── Agents ──────────────────────────────────────────
export const agentApi = {
  list: (tenantId = 'default') => api.get<Agent[]>('/agents/', { params: { tenant_id: tenantId } }),
  create: (data: AgentCreate) => api.post<Agent>('/agents/', data),
  update: (id: string, data: AgentUpdate) => api.put<Agent>(`/agents/${id}`, data),
  delete: (id: string) => api.delete(`/agents/${id}`),
  bulkUpdate: (agentIds: string[], updates: Record<string, unknown>) =>
    api.post('/agents/bulk-update', { agent_ids: agentIds, updates }),
  export: (agentId: string) => api.get(`/agents/${agentId}/export`),
  import: (data: Record<string, unknown>) => api.post('/agents/import', data),
  clone: (agentId: string) => api.post(`/agents/${agentId}/clone`),
};

// ── Agent Capabilities ────────────────────────────
export const agentCapabilitiesApi = {
  get: (agentId: string) => api.get<AgentCapabilities>(`/agents/${agentId}/capabilities`),
  update: (agentId: string, data: AgentCapabilities) => api.put(`/agents/${agentId}/capabilities`, data),
};

// ── Agent Connections ──────────────────────────────
export const agentConnectionApi = {
  list: (tenantId = 'default', agentId?: string) =>
    api.get<AgentConnection[]>('/agent-connections/', { params: { tenant_id: tenantId, agent_id: agentId } }),
  create: (data: AgentConnectionCreate) => api.post<AgentConnection>('/agent-connections/', data),
  delete: (id: string) => api.delete(`/agent-connections/${id}`),
};

// ── Workflows ───────────────────────────────────────
export const workflowApi = {
  list: (tenantId = 'default') => api.get<Workflow[]>('/workflows/', { params: { tenant_id: tenantId } }),
  create: (data: WorkflowCreate) => api.post<Workflow>('/workflows/', data),
  update: (id: string, data: WorkflowUpdate) => api.put<Workflow>(`/workflows/${id}`, data),
  delete: (id: string) => api.delete(`/workflows/${id}`),
  addStep: (workflowId: string, data: StepCreate) => api.post(`/workflows/${workflowId}/steps`, data),
  updateStep: (workflowId: string, stepId: string, data: Partial<StepCreate>) =>
    api.put(`/workflows/${workflowId}/steps/${stepId}`, data),
  deleteStep: (workflowId: string, stepId: string) => api.delete(`/workflows/${workflowId}/steps/${stepId}`),
  // Versioning
  publish: (workflowId: string) => api.post(`/workflows/${workflowId}/publish`),
  listVersions: (workflowId: string) => api.get<WorkflowVersion[]>(`/workflows/${workflowId}/versions`),
  getVersion: (workflowId: string, version: number) =>
    api.get<WorkflowVersion>(`/workflows/${workflowId}/versions/${version}`),
};

// ── Knowledge ───────────────────────────────────────
export const knowledgeApi = {
  listSources: (tenantId = 'default') =>
    api.get<KnowledgeSource[]>('/knowledge/sources', { params: { tenant_id: tenantId } }),
  createSource: (data: KnowledgeSourceCreate) => api.post<KnowledgeSource>('/knowledge/sources', data),
  deleteSource: (id: string) => api.delete(`/knowledge/sources/${id}`),
  addKV: (data: { source_id: string; entity_key: string; content: string; domain?: string }) =>
    api.post('/knowledge/kv', data),
  addFAQ: (data: { source_id: string; question: string; answer: string; domain?: string }) =>
    api.post('/knowledge/faq', data),
  search: (data: { query: string; domain?: string; top_k?: number }) =>
    api.post<RetrievalResponse>('/knowledge/search', data),
  upload: (formData: FormData) =>
    api.post('/knowledge/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    }),
  listChunks: (sourceId: string) => api.get(`/knowledge/sources/${sourceId}/chunks`),
};

// ── Tools ───────────────────────────────────────────
export const toolApi = {
  list: (tenantId = 'default') => api.get<Tool[]>('/tools/', { params: { tenant_id: tenantId } }),
  create: (data: ToolCreate) => api.post<Tool>('/tools/', data),
  update: (id: string, data: ToolUpdate) => api.put<Tool>(`/tools/${id}`, data),
  delete: (id: string) => api.delete(`/tools/${id}`),
  test: (data: { tool_id: string; test_input?: Record<string, unknown> }) => api.post('/tools/test', data),
};

// ── Skills ─────────────────────────────────────────
export const skillApi = {
  list: (tenantId = 'default', managedBy?: string) =>
    api.get<Skill[]>('/skills/', { params: { tenant_id: tenantId, managed_by: managedBy } }),
  create: (data: SkillCreate) => api.post<Skill>('/skills/', data),
  update: (id: string, data: SkillUpdate) => api.put<Skill>(`/skills/${id}`, data),
  delete: (id: string) => api.delete(`/skills/${id}`),
};

// ── Audit ───────────────────────────────────────────
export const auditApi = {
  getTrace: (traceId: string) => api.get<AuditTrace[]>(`/audit/traces/${traceId}`),
  getSessionTraces: (sessionId: string) => api.get<AuditTrace[]>(`/audit/sessions/${sessionId}/traces`),
  getMetrics: (tenantId = 'default', hours = 24) =>
    api.get<AuditMetrics>('/audit/metrics', { params: { tenant_id: tenantId, hours } }),
  listTraces: (params?: { tenant_id?: string; limit?: number; offset?: number; event_type?: string }) =>
    api.get<AuditTraceListResponse>('/audit/traces', { params }),
};

// ── Invoke ─────────────────────────────────────────
export const invokeApi = {
  send: (data: InvokeRequest | Record<string, unknown>) => api.post<InvokeResponse>('/invoke', data),
};

export const asrApi = {
  status: () => api.get('/asr/status'),
  getConfig: () => api.get('/asr/config'),
  updateConfig: (data: Record<string, unknown>) => api.put('/asr/config', data),
  transcribe: (file: File, options?: { language?: string; prompt?: string }) => {
    const formData = new FormData();
    formData.append('file', file);
    if (options?.language) formData.append('language', options.language);
    if (options?.prompt) formData.append('prompt', options.prompt);
    return api.post('/asr/transcribe', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    });
  },
};

// ── LLM Configs ─────────────────────────────────────
export const llmConfigApi = {
  list: (tenantId = 'default') => api.get<LLMConfig[]>('/llm-configs/', { params: { tenant_id: tenantId } }),
  create: (data: LLMConfigCreate) => api.post<LLMConfig>('/llm-configs/', data),
  update: (id: string, data: LLMConfigUpdate) => api.put<LLMConfig>(`/llm-configs/${id}`, data),
  delete: (id: string) => api.delete(`/llm-configs/${id}`),
  setDefault: (id: string) => api.post(`/llm-configs/set-default/${id}`),
  getTemplates: () => api.get<LLMTemplate[]>('/llm-configs/templates'),
  test: (data: Record<string, unknown>) => api.post('/llm-configs/test', data),
};

// ── Performance ─────────────────────────────────────
export const performanceApi = {
  getPresets: () => api.get('/performance/presets'),
  applyPreset: (preset: string) => api.post('/performance/presets/apply', { preset }),
  getCurrentConfig: () => api.get<Record<string, unknown>>('/performance/current-config'),
  updateConfig: (data: Record<string, unknown>) => api.post('/performance/update-config', data),
  getCircuitBreakerStatus: () => api.get('/performance/circuit-breaker/status'),
};

export const vectorAdminApi = {
  getModelStatus: () => api.get('/vector-admin/model-status'),
  warmup: () => api.post('/vector-admin/warmup'),
  getStats: () => api.get('/vector-admin/stats'),
  rebuild: () => api.post('/vector-admin/rebuild'),
  health: () => api.get('/vector-admin/health'),
};

// ── Auth ─────────────────────────────────────────
export const authApi = {
  login: (username: string, password: string) =>
    api.post<{ access_token: string; token_type: string }>('/auth/login', { username, password }),
  register: (data: { username: string; password: string; display_name?: string }) =>
    api.post('/auth/register', data),
  me: () => api.get('/auth/me'),
  listApiKeys: () => api.get('/auth/api-keys'),
  createApiKey: (name: string) => api.post('/auth/api-keys', { name }),
  deleteApiKey: (keyId: string) => api.delete(`/auth/api-keys/${keyId}`),
};

// ── Health ─────────────────────────────────────────
export const healthApi = {
  check: () => axios.get('/health', { timeout: 5000 }),
};

export default api;
