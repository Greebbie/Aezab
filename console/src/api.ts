import axios, { AxiosInstance } from 'axios';
import type {
  Agent, AgentCreate, AgentUpdate, AgentCapabilities, AgentConnection, AgentConnectionCreate,
  Workflow, WorkflowCreate, WorkflowUpdate, StepCreate, WorkflowVersion,
  KnowledgeSource, KnowledgeSourceCreate, RetrievalResponse,
  Tool, ToolCreate, ToolUpdate,
  Skill, SkillCreate, SkillUpdate,
  LLMConfig, LLMConfigCreate, LLMConfigUpdate, LLMTemplate,
  AuditTrace, AuditTraceListResponse, AuditMetrics, UsageSummary,
  InvokeRequest, InvokeResponse,
  TemplateSummary, TemplateInstantiateRequest,
  EventSubscription, EventSubscriptionCreate, EventSubscriptionUpdate,
  AuthStatus, AuthUser, LoginResponse,
  BackupSummary, BackupCreateResult,
  ConversationSessionListResponse, ConversationMessageListResponse,
} from './types';

// ── Token storage ──────────────────────────────────
export const TOKEN_STORAGE_KEY = 'aezab_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

// Response shape for the "which agents use this resource" usage-check
// endpoints (knowledge sources / tools / workflows) — used to warn before
// a destructive delete silently breaks a live agent.
export interface ResourceUsage {
  used_by: { agent_id: string; agent_name: string }[];
  count: number;
}

// HTTP-layer error raised by the response interceptor below. Carries the
// response status (when available) and raw detail alongside the existing
// `message` behavior, so callers that need to branch on status (e.g.
// friendlyError()) don't have to re-parse an Error's string message.
export class ApiError extends Error {
  status?: number;
  detail?: string;

  constructor(message: string, status?: number, detail?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

const api: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

// Attach the bearer token, when present, to every outgoing request
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Global error interceptor
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const isLoginRequest = error.config?.url?.includes('/auth/login');
    if (error.response?.status === 401 && !isLoginRequest) {
      clearToken();
      window.dispatchEvent(new CustomEvent('aezab:unauthorized'));
    }
    const detail = error.response?.data?.detail;
    const message = detail || error.message || 'Unknown error';
    return Promise.reject(new ApiError(message, error.response?.status, detail));
  },
);

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
  usage: (workflowId: string) => api.get<ResourceUsage>(`/workflows/${workflowId}/usage`),
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
  usage: (sourceId: string) => api.get<ResourceUsage>(`/knowledge/sources/${sourceId}/usage`),
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
  usage: (toolId: string) => api.get<ResourceUsage>(`/tools/${toolId}/usage`),
};

// ── Skills ─────────────────────────────────────────
export const skillApi = {
  list: (tenantId = 'default', managedBy?: string) =>
    api.get<Skill[]>('/skills/', { params: { tenant_id: tenantId, managed_by: managedBy } }),
  create: (data: SkillCreate) => api.post<Skill>('/skills/', data),
  update: (id: string, data: SkillUpdate) => api.put<Skill>(`/skills/${id}`, data),
  delete: (id: string) => api.delete(`/skills/${id}`),
};

// ── Agent Templates (傻瓜式 stamping) ────────────────
export const agentTemplatesApi = {
  list: () => api.get<TemplateSummary[]>('/agent-templates/'),
  instantiate: (templateId: string, body: TemplateInstantiateRequest) =>
    api.post<Agent>(`/agent-templates/${templateId}/instantiate`, body),
};

// ── Event Subscriptions (outbound webhooks) ─────────
export const subscriptionsApi = {
  list: (tenantId = 'default') =>
    api.get<EventSubscription[]>('/subscriptions/', { params: { tenant_id: tenantId } }),
  get: (id: string) => api.get<EventSubscription>(`/subscriptions/${id}`),
  create: (data: EventSubscriptionCreate) => api.post<EventSubscription>('/subscriptions/', data),
  update: (id: string, data: EventSubscriptionUpdate) =>
    api.put<EventSubscription>(`/subscriptions/${id}`, data),
  delete: (id: string) => api.delete(`/subscriptions/${id}`),
};

// ── Audit ───────────────────────────────────────────
export const auditApi = {
  getTrace: (traceId: string) => api.get<AuditTrace[]>(`/audit/traces/${traceId}`),
  getSessionTraces: (sessionId: string) => api.get<AuditTrace[]>(`/audit/sessions/${sessionId}/traces`),
  getMetrics: (tenantId = 'default', hours = 24) =>
    api.get<AuditMetrics>('/audit/metrics', { params: { tenant_id: tenantId, hours } }),
  listTraces: (params?: { tenant_id?: string; limit?: number; offset?: number; event_type?: string }) =>
    api.get<AuditTraceListResponse>('/audit/traces', { params }),
  getUsage: (params?: { tenant_id?: string; days?: number }) =>
    api.get<UsageSummary>('/audit/usage', { params }),
};

// ── Conversation Sessions ───────────────────────────
export const sessionsApi = {
  list: (params?: { agent_id?: string; user_id?: string; limit?: number; offset?: number }) =>
    api.get<ConversationSessionListResponse>('/sessions/', { params }),
  messages: (sessionId: string, params?: { limit?: number; offset?: number }) =>
    api.get<ConversationMessageListResponse>(`/sessions/${sessionId}/messages`, { params }),
  delete: (sessionId: string) => api.delete(`/sessions/${sessionId}`),
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

// ── Backups (server/engine/backup.py + server/api/backup.py) ───────
export const backupApi = {
  list: () => api.get<BackupSummary[]>('/backups/'),
  create: () => api.post<BackupCreateResult>('/backups/'),
  delete: (name: string) => api.delete(`/backups/${encodeURIComponent(name)}`),
  // Downloads as a blob rather than window.open()/an <a href> pointing
  // straight at the API — that path sends no Authorization header, and this
  // endpoint requires the admin bearer token (see server/api/backup.py).
  downloadBlob: (name: string) =>
    api.get<Blob>(`/backups/${encodeURIComponent(name)}/download`, { responseType: 'blob' }),
};

// ── Auth ─────────────────────────────────────────
export const authApi = {
  status: () => api.get<AuthStatus>('/auth/status'),
  login: (username: string, password: string) =>
    api.post<LoginResponse>('/auth/login', { username, password }),
  register: (data: { username: string; password: string; display_name?: string }) =>
    api.post<AuthUser>('/auth/register', data),
  me: () => api.get<AuthUser>('/auth/me'),
  listApiKeys: () => api.get('/auth/api-keys'),
  createApiKey: (name: string) => api.post('/auth/api-keys', { name }),
  deleteApiKey: (keyId: string) => api.delete(`/auth/api-keys/${keyId}`),
};

// ── Health ─────────────────────────────────────────
export const healthApi = {
  check: (options?: { checkLlm?: boolean; force?: boolean }) =>
    axios.get('/health', {
      timeout: options?.checkLlm ? 15000 : 5000,
      params: {
        check_llm: options?.checkLlm ?? undefined,
        force: options?.force ?? undefined,
      },
    }),
};

export default api;
