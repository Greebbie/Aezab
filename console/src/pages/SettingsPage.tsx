import React, { useEffect, useState } from 'react';
import {
  Alert, Card, Button, Form, Input, InputNumber, Select, Switch, Space, message, Tag, Tooltip,
  Descriptions, Spin, Row, Col, Divider, Upload, Collapse,
} from 'antd';
import {
  ThunderboltOutlined, DashboardOutlined, SafetyCertificateOutlined,
  AudioOutlined, CheckCircleOutlined, ExperimentOutlined, SettingOutlined, UploadOutlined,
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { asrApi, performanceApi, vectorAdminApi } from '../api';
import BackupCard from '../components/settings/BackupCard';
import { HelpLabel } from '../components/shared';

interface PresetData {
  name: string;
  description: string;
  retrieval_top_k: number;
  retrieval_timeout_ms: number;
  llm_temperature: number;
  llm_max_tokens: number;
  llm_timeout_ms: number;
  tool_timeout_ms: number;
  tool_max_retries: number;
  keyword_weight: number;
  ef_search: number;
  reranker_enabled: boolean;
}

const PRESET_ICONS: Record<string, React.ReactNode> = {
  fast: <ThunderboltOutlined />,
  balanced: <DashboardOutlined />,
  accurate: <SafetyCertificateOutlined />,
};

const PRESET_COLORS: Record<string, string> = {
  fast: '#52c41a',
  balanced: '#1890ff',
  accurate: '#722ed1',
};

const ASR_PROVIDER_OPTIONS = [
  { label: 'DashScope Qwen ASR', value: 'dashscope_qwen' },
  { label: 'OpenAI Compatible', value: 'openai_compatible' },
  { label: 'Self-hosted FunASR HTTP', value: 'funasr_http' },
  { label: 'Disabled', value: 'disabled' },
];

const ASR_PROVIDER_DEFAULTS: Record<string, Record<string, unknown>> = {
  dashscope_qwen: {
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen3-asr-flash',
    timeout: 60,
    max_file_mb: 10,
    funasr_path: '/transcribe',
  },
  openai_compatible: {
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-transcribe',
    timeout: 60,
    max_file_mb: 20,
    funasr_path: '/transcribe',
  },
  funasr_http: {
    base_url: 'http://host.docker.internal:10095',
    model: 'paraformer-zh',
    timeout: 60,
    max_file_mb: 100,
    funasr_path: '/transcribe',
  },
  disabled: {
    base_url: '',
    model: '',
    timeout: 60,
    max_file_mb: 10,
    funasr_path: '/transcribe',
  },
};

const ASR_KEY_SOURCE_LABELS: Record<string, string> = {
  saved_config: 'Saved in console',
  environment: 'Environment variable',
  llm_config: 'LLM configuration',
  missing: 'Missing',
};

const SETTINGS_COPY = {
  zh: {
    pageTitle: '性能与运行设置',
    refresh: '刷新',
    enabled: '已启用',
    disabled: '已关闭',
    ready: '就绪',
    custom: '自定义',
    active: '当前',
    apply: '应用',
    reapply: '重新应用',
    resetToCurrent: '重置为当前值',
    rag: {
      title: 'RAG 检索引擎',
      enableVector: '启用向量检索 / 预热 Embedding',
      infoTitle: 'Fast KV + BM25 部署后即可使用',
      infoDescription:
        'Balanced Mode 是 top_k、超时、关键词权重、工具重试、reranker 行为的运行时预设。Embedding 预热只启用可选向量通道；未启用时，RAG 仍会通过 fast answers 和关键词检索工作。',
      baselineRetrieval: '基础检索',
      baselineReady: 'Fast KV + BM25 就绪',
      vectorChannel: '向量通道',
      vectorOptional: '可选，未加载',
      provider: '提供方',
      configuredModel: '配置模型',
      loaded: '加载状态',
      loadedModel: '已加载模型',
      dimension: '维度',
      hfEndpoint: 'HF Endpoint',
    },
    asr: {
      title: '语音输入 / ASR 配置',
      runtimeStatus: '运行状态',
      currentProvider: '当前提供方',
      apiKey: 'ASR API Key',
      keySource: 'Key 来源',
      consoleSavedConfig: '控制台保存配置',
      configurationFile: '配置文件',
      savedInConsole: '控制台已保存',
      notSavedInConsole: '控制台未保存',
      usingLlmKey: '使用 LLM Key',
      removeSavedKeyHelp:
        '只移除控制台配置文件中保存的 API Key。环境变量需要在 Aezab 外部管理。',
      environmentKeyHelp:
        'ASR 就绪是因为环境变量提供了 Key。控制台没有可移除的保存 Key；请修改 .env 或云端 secrets。',
      noSavedKeyHelp: '控制台配置文件里还没有保存 API Key。',
      disabled: '已关闭',
      ready: '可转写',
      missingKey: '已启用，但缺少 API Key',
      incomplete: '已启用，但配置不完整',
      provider: 'ASR Provider',
      baseUrl: 'ASR Base URL',
      model: 'ASR Model',
      timeout: 'ASR Timeout (seconds)',
      maxFile: 'Max Audio File (MB)',
      funasrPath: 'FunASR HTTP Path',
      selectProvider: '请选择 ASR 提供方',
      baseRequired: '请填写 ASR Base URL',
      modelRequired: '请填写 ASR Model',
      apiPlaceholder: '留空则保留当前 Key',
      save: '保存 ASR 配置',
      applyDefaults: '应用提供方默认值',
      removeSavedKey: '移除已保存 API Key',
      noSavedKey: '没有已保存 API Key',
      testUpload: '测试 ASR 上传',
      testResult: 'ASR 测试结果',
      transcript: '转写文本',
      language: '语言',
    },
    asrKeySources: {
      saved_config: '控制台保存',
      environment: '环境变量',
      llm_config: 'LLM 配置',
      missing: '缺失',
    },
    presets: {
      title: '性能预设',
      retrievalTopK: '返回结果数 (Top-K)',
      llmTemperature: '回答随机度 (Temperature)',
      llmMaxTokens: '最大回复长度 (Max Tokens)',
      llmTimeout: 'LLM 超时时间',
      toolRetries: '工具重试次数',
      keywordWeight: '关键词权重',
      hnswEfSearch: '检索精细度 (efSearch)',
      reranker: '二次精排 (Reranker)',
      // Localized name/description for the well-known preset keys returned
      // by GET /performance/presets (server/performance_presets.py). The
      // backend only ever returns Chinese text for `preset.name`/
      // `preset.description`, so under an English UI these are used instead
      // — see presetName()/presetDescription() below, which fall back to
      // the raw backend string for any preset key not listed here.
      labels: {
        fast: { name: '快速模式', description: '低延迟，适合实时对话' },
        balanced: { name: '均衡模式', description: '兼顾速度与质量' },
        accurate: { name: '精准模式', description: '高质量回答，延迟较高' },
      } as Record<string, { name: string; description: string }>,
    },
    current: {
      title: '当前运行配置',
      activeConfiguration: '当前配置',
    },
    advanced: {
      title: '高级调优',
      save: '保存配置',
      retrievalTopK: '检索返回条数 (Top-K)',
      retrievalTopKHelp: '每次检索给大模型参考的最相关内容条数。调大能提供更多上下文，但会变慢、更占 Token。快速=8 / 均衡=15 / 精准=25。',
      retrievalTimeout: '检索超时时间 (ms)',
      retrievalTimeoutHelp: '检索环节最长等待时间，超过则放弃检索，直接用大模型已有信息回答。',
      keywordWeight: '关键词权重 (Keyword Weight)',
      keywordWeightHelp: '关键词匹配在混合检索里的权重。文档中专业术语、产品型号较多时可以调高。',
      efSearch: '检索精细度 (efSearch)',
      efSearchHelp: '检索精细度：越大越准但越慢。快速预设=64 / 均衡预设=128 / 精准预设=256。',
      llmTemperature: 'LLM 随机度 (Temperature)',
      llmTemperatureHelp: '回答的随机程度，越低越严谨、越高越有创造性，默认即可。',
      llmMaxTokens: 'LLM 最大回复长度 (Max Tokens)',
      llmMaxTokensHelp: '单次回答最多能生成多少内容。调高可以让回答更完整，但更慢、更贵。',
      llmTimeout: 'LLM 超时时间 (ms)',
      llmTimeoutHelp: '等待大模型响应的最长时间。如果经常超时可以调大这个值。',
      toolTimeout: '工具调用超时时间 (ms)',
      toolTimeoutHelp: '调用外部工具 / HTTP 接口时的最长等待时间。',
      toolRetries: '工具调用重试次数',
      toolRetriesHelp: '外部工具调用失败后自动重试的次数。网络不稳定时可以调高。',
      rerankerEnabled: '二次精排 (Reranker)',
      rerankerEnabledHelp: '对检索结果做二次排序，能显著提升准确度，但每次回答会慢约 0.5-1 秒，且首次使用需要下载模型。',
    },
  },
  en: {
    pageTitle: 'Performance Settings',
    refresh: 'Refresh',
    enabled: 'Enabled',
    disabled: 'Disabled',
    ready: 'Ready',
    custom: 'Custom',
    active: 'Active',
    apply: 'Apply',
    reapply: 'Re-apply',
    resetToCurrent: 'Reset to Current',
    rag: {
      title: 'RAG Retrieval Engine',
      enableVector: 'Enable Vector Retrieval / Warm Up Embedding',
      infoTitle: 'Fast KV + BM25 are available immediately',
      infoDescription:
        'Balanced Mode is a runtime preset for top_k, timeouts, keyword weight, tool retries, and reranker behavior. Embedding warmup only enables the optional vector channel; RAG still works without it through fast answers and keyword retrieval.',
      baselineRetrieval: 'Baseline Retrieval',
      baselineReady: 'Fast KV + BM25 ready',
      vectorChannel: 'Vector Channel',
      vectorOptional: 'Optional, not loaded',
      provider: 'Provider',
      configuredModel: 'Configured Model',
      loaded: 'Loaded',
      loadedModel: 'Loaded Model',
      dimension: 'Dimension',
      hfEndpoint: 'HF Endpoint',
    },
    asr: {
      title: 'Voice Input / ASR Configuration',
      runtimeStatus: 'Runtime Status',
      currentProvider: 'Current Provider',
      apiKey: 'ASR API Key',
      keySource: 'Key Source',
      consoleSavedConfig: 'Console Saved Config',
      configurationFile: 'Configuration File',
      savedInConsole: 'Saved in console',
      notSavedInConsole: 'Not saved in console',
      usingLlmKey: 'Using LLM key',
      removeSavedKeyHelp:
        'Remove only the API key saved in the console config file. Environment variables are managed outside Aezab.',
      environmentKeyHelp:
        'ASR is ready because an environment variable provides the key. There is no console-saved key to remove; edit .env or cloud secrets to change it.',
      noSavedKeyHelp: 'No API key has been saved in the console config file.',
      disabled: 'Disabled',
      ready: 'Ready to transcribe',
      missingKey: 'Provider enabled, API key missing',
      incomplete: 'Provider enabled, configuration incomplete',
      provider: 'ASR Provider',
      baseUrl: 'ASR Base URL',
      model: 'ASR Model',
      timeout: 'ASR Timeout (seconds)',
      maxFile: 'Max Audio File (MB)',
      funasrPath: 'FunASR HTTP Path',
      selectProvider: 'Select an ASR provider',
      baseRequired: 'ASR base URL is required',
      modelRequired: 'ASR model is required',
      apiPlaceholder: 'Leave blank to keep current key',
      save: 'Save ASR Configuration',
      applyDefaults: 'Apply Provider Defaults',
      removeSavedKey: 'Remove Saved API Key',
      noSavedKey: 'No Saved API Key to Remove',
      testUpload: 'Test ASR Upload',
      testResult: 'ASR Test Result',
      transcript: 'Transcript',
      language: 'Language',
    },
    asrKeySources: ASR_KEY_SOURCE_LABELS,
    presets: {
      title: 'Presets',
      retrievalTopK: 'Results Used (Top-K)',
      llmTemperature: 'Answer Randomness (Temperature)',
      llmMaxTokens: 'Max Reply Length (Max Tokens)',
      llmTimeout: 'LLM Timeout',
      toolRetries: 'Tool Retries',
      keywordWeight: 'Keyword Weight',
      hnswEfSearch: 'Search Precision (efSearch)',
      reranker: 'Second-pass Reranking (Reranker)',
      labels: {
        fast: { name: 'Fast Mode', description: 'Low latency, good for real-time conversation' },
        balanced: { name: 'Balanced Mode', description: 'Balances speed and quality' },
        accurate: { name: 'Accurate Mode', description: 'Higher-quality answers, higher latency' },
      } as Record<string, { name: string; description: string }>,
    },
    current: {
      title: 'Current Runtime Configuration',
      activeConfiguration: 'Active Configuration',
    },
    advanced: {
      title: 'Advanced Tuning',
      save: 'Save Configuration',
      retrievalTopK: 'Retrieval Results (Top-K)',
      retrievalTopKHelp: 'How many of the most relevant chunks are handed to the model per query. Higher gives more context but is slower and uses more tokens. Fast = 8 / Balanced = 15 / Accurate = 25.',
      retrievalTimeout: 'Retrieval Timeout (ms)',
      retrievalTimeoutHelp: 'The longest retrieval is allowed to take before the system gives up and answers with whatever the model already knows.',
      keywordWeight: 'Keyword Weight',
      keywordWeightHelp: "How much keyword matching counts in the blended search. Raise it when your documents are full of technical terms or product codes.",
      efSearch: 'Search Precision (efSearch)',
      efSearchHelp: 'Search precision: higher is more accurate but slower. Fast preset = 64 / Balanced preset = 128 / Accurate preset = 256.',
      llmTemperature: 'Answer Randomness (Temperature)',
      llmTemperatureHelp: 'How random the answers are. Lower is more precise and consistent, higher is more creative. The default works for most cases.',
      llmMaxTokens: 'Max Reply Length (Max Tokens)',
      llmMaxTokensHelp: 'The maximum amount of content a single answer can generate. Higher allows more complete answers, but is slower and costs more.',
      llmTimeout: 'LLM Timeout (ms)',
      llmTimeoutHelp: 'How long to wait for the model before giving up. Increase this if you see timeout errors.',
      toolTimeout: 'Tool Call Timeout (ms)',
      toolTimeoutHelp: 'The longest wait allowed when calling an external tool or HTTP endpoint.',
      toolRetries: 'Tool Retry Count',
      toolRetriesHelp: 'How many times a failed external tool call is automatically retried. Raise this on unreliable networks.',
      rerankerEnabled: 'Second-pass Reranking (Reranker)',
      rerankerEnabledHelp: 'Re-scores retrieval results for a noticeable accuracy boost, at the cost of about 0.5-1 extra second per answer, and a one-time model download the first time it runs.',
    },
  },
} as const;

type SettingsCopy = (typeof SETTINGS_COPY)[keyof typeof SETTINGS_COPY];

function presetName(key: string, backendName: string, copy: SettingsCopy): string {
  return copy.presets.labels[key]?.name ?? backendName;
}

function presetDescription(key: string, backendDescription: string, copy: SettingsCopy): string {
  return copy.presets.labels[key]?.description ?? backendDescription;
}

function asrRuntimeStatusText(status: Record<string, any>, copy: SettingsCopy) {
  if (!status.enabled) return copy.asr.disabled;
  if (status.ready) return copy.asr.ready;
  if (status.needs_api_key && !status.has_api_key) return copy.asr.missingKey;
  return copy.asr.incomplete;
}

function asrRuntimeStatusColor(status: Record<string, any>) {
  if (!status.enabled) return 'default';
  if (status.ready) return 'green';
  return 'orange';
}

export default function SettingsPage() {
  const { i18n } = useTranslation();
  const copy: SettingsCopy = i18n.language === 'zh' ? SETTINGS_COPY.zh : SETTINGS_COPY.en;
  const [presets, setPresets] = useState<Record<string, PresetData>>({});
  const [currentConfig, setCurrentConfig] = useState<Record<string, any>>({});
  const [loadingPresets, setLoadingPresets] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [applyingPreset, setApplyingPreset] = useState<string | null>(null);
  const [savingConfig, setSavingConfig] = useState(false);
  const [modelStatus, setModelStatus] = useState<Record<string, any>>({});
  const [loadingModelStatus, setLoadingModelStatus] = useState(false);
  const [warmingModel, setWarmingModel] = useState(false);
  const [asrStatus, setAsrStatus] = useState<Record<string, any>>({});
  const [loadingAsrStatus, setLoadingAsrStatus] = useState(false);
  const [savingAsrConfig, setSavingAsrConfig] = useState(false);
  const [testingAsr, setTestingAsr] = useState(false);
  const [asrTestResult, setAsrTestResult] = useState<Record<string, any> | null>(null);
  const [form] = Form.useForm();
  const [asrForm] = Form.useForm();
  const asrProvider = Form.useWatch('provider', asrForm);

  const loadPresets = async () => {
    setLoadingPresets(true);
    try {
      const res = await performanceApi.getPresets();
      setPresets(res.data);
    } catch {
      message.error('Failed to load performance presets');
    } finally {
      setLoadingPresets(false);
    }
  };

  const loadCurrentConfig = async () => {
    setLoadingConfig(true);
    try {
      const res = await performanceApi.getCurrentConfig();
      setCurrentConfig(res.data);
      form.setFieldsValue(res.data);
    } catch {
      message.error('Failed to load current configuration');
    } finally {
      setLoadingConfig(false);
    }
  };

  useEffect(() => {
    loadPresets();
    loadCurrentConfig();
    loadModelStatus();
    loadAsrStatus();
  }, []);

  const loadModelStatus = async () => {
    setLoadingModelStatus(true);
    try {
      const res = await vectorAdminApi.getModelStatus();
      setModelStatus(res.data);
    } catch {
      message.error('Failed to load embedding model status');
    } finally {
      setLoadingModelStatus(false);
    }
  };

  const removeSavedAsrKeyHelp = asrStatus.has_saved_api_key
    ? copy.asr.removeSavedKeyHelp
    : asrStatus.api_key_source === 'environment'
      ? copy.asr.environmentKeyHelp
      : copy.asr.noSavedKeyHelp;

  const handleWarmupModel = async () => {
    setWarmingModel(true);
    try {
      const res = await vectorAdminApi.warmup();
      setModelStatus({
        ...modelStatus,
        loaded: res.data.status === 'ready',
        loaded_model: res.data.loaded_model,
        loaded_dimension: res.data.dimension,
        hf_endpoint: res.data.hf_endpoint,
      });
      if (res.data.status === 'ready') {
        message.success(`Embedding model ready: ${res.data.loaded_model || 'configured model'}`);
      } else {
        message.warning(res.data.message || 'Embedding model is not ready');
      }
    } catch (err: any) {
      message.error('Failed to warm up embedding model: ' + (err?.response?.data?.detail || err.message));
    } finally {
      setWarmingModel(false);
    }
  };

  const loadAsrStatus = async () => {
    setLoadingAsrStatus(true);
    try {
      const res = await asrApi.getConfig();
      setAsrStatus(res.data);
      asrForm.setFieldsValue({
        provider: res.data.provider,
        base_url: res.data.base_url,
        model: res.data.model,
        timeout: res.data.timeout,
        max_file_mb: res.data.max_file_mb,
        funasr_path: res.data.funasr_path,
        api_key: '',
      });
    } catch {
      message.error('Failed to load ASR status');
    } finally {
      setLoadingAsrStatus(false);
    }
  };

  const handleSaveAsrConfig = async () => {
    setSavingAsrConfig(true);
    try {
      const values = await asrForm.validateFields();
      const payload = { ...values };
      if (!payload.api_key) delete payload.api_key;
      const res = await asrApi.updateConfig(payload);
      setAsrStatus(res.data);
      asrForm.setFieldsValue({
        ...res.data,
        api_key: '',
      });
      message.success('ASR configuration saved');
    } catch (err: any) {
      if (err?.errorFields) {
        setSavingAsrConfig(false);
        return;
      }
      message.error('Failed to save ASR configuration: ' + (err?.response?.data?.detail || err.message));
    } finally {
      setSavingAsrConfig(false);
    }
  };

  const handleRemoveSavedAsrKey = async () => {
    setSavingAsrConfig(true);
    try {
      const res = await asrApi.updateConfig({
        provider: asrStatus.provider,
        base_url: asrStatus.base_url,
        model: asrStatus.model,
        timeout: asrStatus.timeout,
        max_file_mb: asrStatus.max_file_mb,
        funasr_path: asrStatus.funasr_path,
        clear_api_key: true,
      });
      setAsrStatus(res.data);
      asrForm.setFieldsValue({
        ...res.data,
        api_key: '',
      });
      message.success('Saved ASR API key removed');
    } catch (err: any) {
      message.error('Failed to remove saved ASR key: ' + (err?.response?.data?.detail || err.message));
    } finally {
      setSavingAsrConfig(false);
    }
  };

  const applyAsrProviderDefaults = (provider: string) => {
    const defaults = ASR_PROVIDER_DEFAULTS[provider];
    if (!defaults) return;
    asrForm.setFieldsValue({ provider, ...defaults });
  };

  const handleAsrProviderChange = (provider: string) => {
    applyAsrProviderDefaults(provider);
  };

  const handleTestAsrUpload = async (file: File) => {
    setTestingAsr(true);
    setAsrTestResult(null);
    try {
      const res = await asrApi.transcribe(file);
      setAsrTestResult(res.data);
      message.success('ASR test passed');
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err.message || 'unknown error';
      setAsrTestResult({ error: detail });
      message.error('ASR test failed: ' + detail);
    } finally {
      setTestingAsr(false);
    }
    return Upload.LIST_IGNORE;
  };

  const handleApplyPreset = async (presetKey: string) => {
    setApplyingPreset(presetKey);
    try {
      const res = await performanceApi.applyPreset(presetKey);
      message.success(`Preset "${presets[presetKey]?.name || presetKey}" applied successfully`);
      setCurrentConfig(res.data.config);
      form.setFieldsValue(res.data.config);
    } catch (err: any) {
      message.error('Failed to apply preset: ' + (err?.response?.data?.detail || err.message));
    } finally {
      setApplyingPreset(null);
    }
  };

  const handleSaveConfig = async () => {
    setSavingConfig(true);
    try {
      const values = await form.validateFields();
      const res = await performanceApi.updateConfig({ config: values });
      message.success('Configuration updated successfully');
      setCurrentConfig(res.data.config);
    } catch (err: any) {
      if (err?.errorFields) {
        setSavingConfig(false);
        return;
      }
      message.error('Failed to update configuration: ' + (err?.response?.data?.detail || err.message));
    } finally {
      setSavingConfig(false);
    }
  };

  const activePreset = currentConfig.active_preset;

  return (
    <div>
      <h2>{copy.pageTitle}</h2>

      <Card
        title={<Space><SettingOutlined />{copy.rag.title}</Space>}
        extra={
          <Space>
            <Button onClick={loadModelStatus} loading={loadingModelStatus}>{copy.refresh}</Button>
            <Button type="primary" onClick={handleWarmupModel} loading={warmingModel}>
              {copy.rag.enableVector}
            </Button>
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Alert
          showIcon
          type="info"
          style={{ marginBottom: 16 }}
          message={copy.rag.infoTitle}
          description={copy.rag.infoDescription}
        />
        <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }}>
          <Descriptions.Item label={copy.rag.baselineRetrieval}>
            <Tag color="green">{copy.rag.baselineReady}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label={copy.rag.vectorChannel}>
            <Tag color={modelStatus.loaded ? 'green' : 'default'}>
              {modelStatus.loaded ? copy.ready : copy.rag.vectorOptional}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label={copy.rag.provider}>{modelStatus.provider || '-'}</Descriptions.Item>
          <Descriptions.Item label={copy.rag.configuredModel}>{modelStatus.configured_model || '-'}</Descriptions.Item>
          <Descriptions.Item label={copy.rag.loaded}>
            <Tag color={modelStatus.loaded ? 'green' : 'default'}>
              {modelStatus.loaded ? copy.ready : copy.rag.vectorOptional}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label={copy.rag.loadedModel}>{modelStatus.loaded_model || '-'}</Descriptions.Item>
          <Descriptions.Item label={copy.rag.dimension}>
            {modelStatus.loaded_dimension || modelStatus.configured_dimension || '-'}
          </Descriptions.Item>
          <Descriptions.Item label={copy.rag.hfEndpoint}>{modelStatus.hf_endpoint || '-'}</Descriptions.Item>
        </Descriptions>
      </Card>

      {/* ── Preset Cards ─────────────────────────────────── */}
      <Card
        title={<Space><AudioOutlined />{copy.asr.title}</Space>}
        extra={<Button onClick={loadAsrStatus} loading={loadingAsrStatus}>{copy.refresh}</Button>}
        style={{ marginBottom: 24 }}
      >
        <Spin spinning={loadingAsrStatus}>
          <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }} style={{ marginBottom: 16 }}>
            <Descriptions.Item label={copy.asr.runtimeStatus}>
              <Tag color={asrRuntimeStatusColor(asrStatus)}>
                {asrRuntimeStatusText(asrStatus, copy)}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label={copy.asr.currentProvider}>{asrStatus.provider || '-'}</Descriptions.Item>
            <Descriptions.Item label={copy.asr.apiKey}>
              <Tag color={asrStatus.has_api_key ? 'green' : 'orange'}>
                {copy.asrKeySources[asrStatus.api_key_source as keyof typeof copy.asrKeySources] || asrStatus.api_key_source || copy.asrKeySources.missing}
              </Tag>
              {asrStatus.uses_llm_api_key && <Tag color="blue">{copy.asr.usingLlmKey}</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label={copy.asr.keySource}>
              <Tag color={asrStatus.api_key_source === 'saved_config' ? 'purple' : 'blue'}>
                {copy.asrKeySources[asrStatus.api_key_source as keyof typeof copy.asrKeySources] || asrStatus.api_key_source || copy.asrKeySources.missing}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label={copy.asr.consoleSavedConfig}>
              <Tag color={asrStatus.has_saved_config ? 'blue' : 'default'}>
                {asrStatus.has_saved_config ? copy.asr.savedInConsole : copy.asr.notSavedInConsole}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label={copy.asr.configurationFile}>{asrStatus.config_path || '-'}</Descriptions.Item>
          </Descriptions>

          <Form form={asrForm} layout="vertical">
            <Row gutter={16}>
              <Col xs={24} md={8}>
                <Form.Item
                  name="provider"
                  label={copy.asr.provider}
                  rules={[{ required: true, message: copy.asr.selectProvider }]}
                >
                  <Select options={ASR_PROVIDER_OPTIONS} onChange={handleAsrProviderChange} />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item
                  name="base_url"
                  label={copy.asr.baseUrl}
                  rules={[{ required: asrProvider !== 'disabled', message: copy.asr.baseRequired }]}
                >
                  <Input placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item
                  name="model"
                  label={copy.asr.model}
                  rules={[{ required: asrProvider !== 'disabled', message: copy.asr.modelRequired }]}
                >
                  <Input placeholder="qwen3-asr-flash" />
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col xs={24} md={8}>
                <Form.Item name="api_key" label={copy.asr.apiKey}>
                  <Input.Password placeholder={copy.asr.apiPlaceholder} autoComplete="new-password" />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item name="timeout" label={copy.asr.timeout}>
                  <InputNumber min={5} max={300} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item name="max_file_mb" label={copy.asr.maxFile}>
                  <InputNumber min={1} max={200} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col xs={24} md={8}>
                <Form.Item name="funasr_path" label={copy.asr.funasrPath}>
                  <Input placeholder="/transcribe" />
                </Form.Item>
              </Col>
            </Row>

            <Space>
              <Button type="primary" onClick={handleSaveAsrConfig} loading={savingAsrConfig}>
                {copy.asr.save}
              </Button>
              <Button onClick={() => applyAsrProviderDefaults(asrProvider)} disabled={!asrProvider}>
                {copy.asr.applyDefaults}
              </Button>
              <Tooltip title={removeSavedAsrKeyHelp}>
                <span>
                  <Button
                    danger
                    onClick={handleRemoveSavedAsrKey}
                    loading={savingAsrConfig}
                    disabled={!asrStatus.has_saved_api_key}
                  >
                    {asrStatus.has_saved_api_key ? copy.asr.removeSavedKey : copy.asr.noSavedKey}
                  </Button>
                </span>
              </Tooltip>
              <Upload
                accept="audio/*,.wav,.mp3,.m4a,.aac,.ogg,.opus,.flac,.webm"
                beforeUpload={handleTestAsrUpload}
                showUploadList={false}
                disabled={testingAsr || !asrStatus.ready}
              >
                <Button icon={<UploadOutlined />} loading={testingAsr} disabled={testingAsr || !asrStatus.ready}>
                  {copy.asr.testUpload}
                </Button>
              </Upload>
              <Button onClick={loadAsrStatus}>{copy.resetToCurrent}</Button>
            </Space>

            {asrTestResult && (
              <Card size="small" style={{ marginTop: 16 }} title={<Space><ExperimentOutlined />{copy.asr.testResult}</Space>}>
                {asrTestResult.error ? (
                  <Tag color="red">{asrTestResult.error}</Tag>
                ) : (
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label={copy.asr.transcript}>{asrTestResult.text || '-'}</Descriptions.Item>
                    <Descriptions.Item label={copy.rag.provider}>{asrTestResult.provider || '-'}</Descriptions.Item>
                    <Descriptions.Item label={copy.asr.model}>{asrTestResult.model || '-'}</Descriptions.Item>
                    <Descriptions.Item label={copy.asr.language}>{asrTestResult.language || '-'}</Descriptions.Item>
                  </Descriptions>
                )}
              </Card>
            )}
          </Form>
        </Spin>
      </Card>

      <Divider orientation="left">{copy.presets.title}</Divider>
      <Spin spinning={loadingPresets}>
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          {Object.entries(presets).map(([key, preset]) => (
            <Col xs={24} sm={8} key={key}>
              <Card
                hoverable
                style={{
                  borderColor: activePreset === key ? PRESET_COLORS[key] : undefined,
                  borderWidth: activePreset === key ? 2 : 1,
                }}
                title={
                  <Space>
                    {PRESET_ICONS[key]}
                    <span>{presetName(key, preset.name, copy)}</span>
                    {activePreset === key && (
                      <Tag color="success" icon={<CheckCircleOutlined />}>{copy.active}</Tag>
                    )}
                  </Space>
                }
                actions={[
                  <Button
                    key="apply"
                    type={activePreset === key ? 'default' : 'primary'}
                    loading={applyingPreset === key}
                    disabled={applyingPreset !== null && applyingPreset !== key}
                    onClick={() => handleApplyPreset(key)}
                    style={{ borderColor: PRESET_COLORS[key], color: activePreset === key ? undefined : undefined }}
                  >
                    {activePreset === key ? copy.reapply : copy.apply}
                  </Button>,
                ]}
              >
                <p style={{ color: '#666', marginBottom: 12 }}>{presetDescription(key, preset.description, copy)}</p>
                <Descriptions column={1} size="small" colon>
                  <Descriptions.Item label={<HelpLabel label={copy.presets.retrievalTopK} help={copy.advanced.retrievalTopKHelp} />}>{preset.retrieval_top_k}</Descriptions.Item>
                  <Descriptions.Item label={<HelpLabel label={copy.presets.llmTemperature} help={copy.advanced.llmTemperatureHelp} />}>{preset.llm_temperature}</Descriptions.Item>
                  <Descriptions.Item label={<HelpLabel label={copy.presets.llmMaxTokens} help={copy.advanced.llmMaxTokensHelp} />}>{preset.llm_max_tokens}</Descriptions.Item>
                  <Descriptions.Item label={<HelpLabel label={copy.presets.llmTimeout} help={copy.advanced.llmTimeoutHelp} />}>{(preset.llm_timeout_ms / 1000).toFixed(0)}s</Descriptions.Item>
                  <Descriptions.Item label={<HelpLabel label={copy.presets.toolRetries} help={copy.advanced.toolRetriesHelp} />}>{preset.tool_max_retries}</Descriptions.Item>
                  <Descriptions.Item label={<HelpLabel label={copy.presets.keywordWeight} help={copy.advanced.keywordWeightHelp} />}>{preset.keyword_weight}</Descriptions.Item>
                  <Descriptions.Item label={<HelpLabel label={copy.presets.hnswEfSearch} help={copy.advanced.efSearchHelp} />}>{preset.ef_search}</Descriptions.Item>
                  <Descriptions.Item label={<HelpLabel label={copy.presets.reranker} help={copy.advanced.rerankerEnabledHelp} />}>
                    <Tag color={preset.reranker_enabled ? 'green' : 'default'}>
                      {preset.reranker_enabled ? copy.enabled : copy.disabled}
                    </Tag>
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
          ))}
        </Row>
      </Spin>

      {/* ── Current Config ───────────────────────────────── */}
      <Divider orientation="left">{copy.current.title}</Divider>
      <Spin spinning={loadingConfig}>
        <Card style={{ marginBottom: 24 }}>
          <Descriptions
            bordered
            column={{ xs: 1, sm: 2, md: 3 }}
            size="small"
            title={
              <Space>
                <SettingOutlined />
                <span>{copy.current.activeConfiguration}</span>
                {activePreset ? (
                  <Tag color={PRESET_COLORS[activePreset] || 'blue'}>
                    {presets[activePreset] ? presetName(activePreset, presets[activePreset].name, copy) : activePreset}
                  </Tag>
                ) : (
                  <Tag color="orange">{copy.custom}</Tag>
                )}
              </Space>
            }
          >
            {Object.entries(currentConfig)
              .filter(([key]) => key !== 'active_preset' && key !== 'name' && key !== 'description')
              .map(([key, value]) => (
                <Descriptions.Item key={key} label={key}>
                  {typeof value === 'boolean' ? (
                    <Tag color={value ? 'green' : 'default'}>{value ? copy.enabled : copy.disabled}</Tag>
                  ) : (
                    String(value)
                  )}
                </Descriptions.Item>
              ))}
          </Descriptions>
        </Card>
      </Spin>

      {/* ── Advanced Tuning ──────────────────────────────── */}
      <Collapse
        style={{ marginBottom: 24 }}
        items={[{
          key: 'advanced-tuning',
          label: copy.advanced.title,
          children: (
            <Form form={form} layout="vertical">
              <Row gutter={24}>
                <Col xs={24} sm={12} md={8}>
                  <Form.Item name="retrieval_top_k" label={<HelpLabel label={copy.advanced.retrievalTopK} help={copy.advanced.retrievalTopKHelp} />}>
                    <InputNumber min={1} max={50} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12} md={8}>
                  <Form.Item name="retrieval_timeout_ms" label={<HelpLabel label={copy.advanced.retrievalTimeout} help={copy.advanced.retrievalTimeoutHelp} />}>
                    <InputNumber min={1000} max={120000} step={1000} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12} md={8}>
                  <Form.Item name="keyword_weight" label={<HelpLabel label={copy.advanced.keywordWeight} help={copy.advanced.keywordWeightHelp} />}>
                    <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={24}>
                <Col xs={24} sm={12} md={8}>
                  <Form.Item name="ef_search" label={<HelpLabel label={copy.advanced.efSearch} help={copy.advanced.efSearchHelp} />}>
                    <InputNumber min={16} max={512} step={16} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12} md={8}>
                  <Form.Item name="llm_temperature" label={<HelpLabel label={copy.advanced.llmTemperature} help={copy.advanced.llmTemperatureHelp} />}>
                    <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12} md={8}>
                  <Form.Item name="llm_max_tokens" label={<HelpLabel label={copy.advanced.llmMaxTokens} help={copy.advanced.llmMaxTokensHelp} />}>
                    <InputNumber min={1} max={128000} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12} md={8}>
                  <Form.Item name="llm_timeout_ms" label={<HelpLabel label={copy.advanced.llmTimeout} help={copy.advanced.llmTimeoutHelp} />}>
                    <InputNumber min={1000} max={600000} step={1000} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={24}>
                <Col xs={24} sm={12} md={8}>
                  <Form.Item name="tool_timeout_ms" label={<HelpLabel label={copy.advanced.toolTimeout} help={copy.advanced.toolTimeoutHelp} />}>
                    <InputNumber min={1000} max={300000} step={1000} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12} md={8}>
                  <Form.Item name="tool_max_retries" label={<HelpLabel label={copy.advanced.toolRetries} help={copy.advanced.toolRetriesHelp} />}>
                    <InputNumber min={0} max={10} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12} md={8}>
                  <Form.Item name="reranker_enabled" label={<HelpLabel label={copy.advanced.rerankerEnabled} help={copy.advanced.rerankerEnabledHelp} />} valuePropName="checked">
                    <Switch />
                  </Form.Item>
                </Col>
              </Row>

              <Space>
                <Button type="primary" onClick={handleSaveConfig} loading={savingConfig}>
                  {copy.advanced.save}
                </Button>
                <Button onClick={loadCurrentConfig}>
                  {copy.resetToCurrent}
                </Button>
              </Space>
            </Form>
          ),
        }]}
      />

      {/* ── Data Backups ──────────────────────────────────── */}
      <BackupCard />
    </div>
  );
}
