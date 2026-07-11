/**
 * Provider presets shown as cards in Step 1 of the SetupWizard.
 *
 * `fallback` mirrors `PROVIDER_TEMPLATES` in server/api/llm_configs.py so the
 * wizard still works if `GET /llm-configs/templates` is unreachable. When the
 * template endpoint responds, its values win over these fallbacks.
 */

export interface ProviderFallback {
  provider: string;
  base_url: string;
  model: string;
}

export interface ProviderPreset {
  id: string;
  /** Key into the PROVIDER_TEMPLATES dict returned by the backend. */
  templateKey: string;
  needsApiKey: boolean;
  /** Whether the base URL field is shown and editable (local/custom providers). */
  editableBaseUrl: boolean;
  fallback: ProviderFallback;
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: 'dashscope',
    templateKey: 'dashscope',
    needsApiKey: true,
    editableBaseUrl: false,
    fallback: {
      provider: 'dashscope',
      base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      model: 'qwen-flash',
    },
  },
  {
    id: 'zhipu',
    templateKey: 'zhipu',
    needsApiKey: true,
    editableBaseUrl: false,
    fallback: {
      provider: 'zhipu',
      base_url: 'https://open.bigmodel.cn/api/paas/v4',
      model: 'glm-4',
    },
  },
  {
    id: 'minimax',
    templateKey: 'minimax',
    needsApiKey: true,
    editableBaseUrl: false,
    fallback: {
      provider: 'openai_compatible',
      base_url: 'https://api.minimaxi.com/v1',
      model: 'MiniMax-M2',
    },
  },
  {
    id: 'openai',
    templateKey: 'openai_compatible',
    needsApiKey: true,
    editableBaseUrl: false,
    fallback: {
      provider: 'openai_compatible',
      base_url: 'https://api.openai.com/v1',
      model: 'gpt-4o',
    },
  },
  {
    id: 'ollama',
    templateKey: 'ollama',
    needsApiKey: false,
    editableBaseUrl: true,
    fallback: {
      provider: 'local',
      base_url: 'http://localhost:11434/v1',
      model: 'qwen2.5',
    },
  },
  {
    id: 'custom',
    templateKey: 'openai_compatible',
    needsApiKey: true,
    editableBaseUrl: true,
    fallback: {
      provider: 'openai_compatible',
      base_url: '',
      model: '',
    },
  },
];
