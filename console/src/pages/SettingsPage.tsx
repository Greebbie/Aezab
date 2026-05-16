import React, { useEffect, useState } from 'react';
import {
  Card, Button, Form, Input, InputNumber, Select, Switch, Space, message, Tag,
  Descriptions, Spin, Row, Col, Divider, Upload,
} from 'antd';
import {
  ThunderboltOutlined, DashboardOutlined, SafetyCertificateOutlined,
  AudioOutlined, CheckCircleOutlined, ExperimentOutlined, SettingOutlined, UploadOutlined,
} from '@ant-design/icons';
import { asrApi, performanceApi, vectorAdminApi } from '../api';

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

export default function SettingsPage() {
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
      <h2>Performance Settings</h2>

      <Card
        title={<Space><SettingOutlined />Embedding Model</Space>}
        extra={
          <Space>
            <Button onClick={loadModelStatus} loading={loadingModelStatus}>Refresh</Button>
            <Button type="primary" onClick={handleWarmupModel} loading={warmingModel}>
              Warm Up / Download
            </Button>
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }}>
          <Descriptions.Item label="Provider">{modelStatus.provider || '-'}</Descriptions.Item>
          <Descriptions.Item label="Configured Model">{modelStatus.configured_model || '-'}</Descriptions.Item>
          <Descriptions.Item label="Loaded">
            <Tag color={modelStatus.loaded ? 'green' : 'default'}>
              {modelStatus.loaded ? 'Ready' : 'Not loaded'}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Loaded Model">{modelStatus.loaded_model || '-'}</Descriptions.Item>
          <Descriptions.Item label="Dimension">
            {modelStatus.loaded_dimension || modelStatus.configured_dimension || '-'}
          </Descriptions.Item>
          <Descriptions.Item label="HF Endpoint">{modelStatus.hf_endpoint || '-'}</Descriptions.Item>
        </Descriptions>
      </Card>

      {/* ── Preset Cards ─────────────────────────────────── */}
      <Card
        title={<Space><AudioOutlined />Voice Input / ASR Configuration</Space>}
        extra={<Button onClick={loadAsrStatus} loading={loadingAsrStatus}>Refresh</Button>}
        style={{ marginBottom: 24 }}
      >
        <Spin spinning={loadingAsrStatus}>
          <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="Status">
              <Tag color={asrStatus.enabled ? 'green' : 'default'}>
                {asrStatus.enabled ? 'Enabled' : 'Disabled'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Current Provider">{asrStatus.provider || '-'}</Descriptions.Item>
            <Descriptions.Item label="API Key">
              <Tag color={asrStatus.has_api_key ? 'green' : 'orange'}>
                {asrStatus.has_api_key ? 'Configured' : 'Missing'}
              </Tag>
              {asrStatus.uses_llm_api_key && <Tag color="blue">Using LLM key</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="Key Source">
              <Tag color={asrStatus.api_key_source === 'saved_config' ? 'purple' : 'blue'}>
                {asrStatus.api_key_source || 'missing'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Saved Overrides">
              <Tag color={asrStatus.has_saved_config ? 'blue' : 'default'}>
                {asrStatus.has_saved_config ? 'Saved' : 'Environment defaults'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Configuration File">{asrStatus.config_path || '-'}</Descriptions.Item>
          </Descriptions>

          <Form form={asrForm} layout="vertical">
            <Row gutter={16}>
              <Col xs={24} md={8}>
                <Form.Item
                  name="provider"
                  label="ASR Provider"
                  rules={[{ required: true, message: 'Select an ASR provider' }]}
                >
                  <Select options={ASR_PROVIDER_OPTIONS} onChange={handleAsrProviderChange} />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item
                  name="base_url"
                  label="ASR Base URL"
                  rules={[{ required: asrProvider !== 'disabled', message: 'ASR base URL is required' }]}
                >
                  <Input placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item
                  name="model"
                  label="ASR Model"
                  rules={[{ required: asrProvider !== 'disabled', message: 'ASR model is required' }]}
                >
                  <Input placeholder="qwen3-asr-flash" />
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col xs={24} md={8}>
                <Form.Item name="api_key" label="ASR API Key">
                  <Input.Password placeholder="Leave blank to keep current key" autoComplete="new-password" />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item name="timeout" label="ASR Timeout (seconds)">
                  <InputNumber min={5} max={300} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item name="max_file_mb" label="Max Audio File (MB)">
                  <InputNumber min={1} max={200} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col xs={24} md={8}>
                <Form.Item name="funasr_path" label="FunASR HTTP Path">
                  <Input placeholder="/transcribe" />
                </Form.Item>
              </Col>
            </Row>

            <Space>
              <Button type="primary" onClick={handleSaveAsrConfig} loading={savingAsrConfig}>
                Save ASR Configuration
              </Button>
              <Button onClick={() => applyAsrProviderDefaults(asrProvider)} disabled={!asrProvider}>
                Apply Provider Defaults
              </Button>
              <Button
                danger
                onClick={handleRemoveSavedAsrKey}
                loading={savingAsrConfig}
                disabled={!asrStatus.has_saved_api_key}
              >
                Remove Saved API Key
              </Button>
              <Upload
                accept="audio/*,.wav,.mp3,.m4a,.aac,.ogg,.opus,.flac,.webm"
                beforeUpload={handleTestAsrUpload}
                showUploadList={false}
                disabled={testingAsr || !asrStatus.enabled}
              >
                <Button icon={<UploadOutlined />} loading={testingAsr} disabled={testingAsr || !asrStatus.enabled}>
                  Test ASR Upload
                </Button>
              </Upload>
              <Button onClick={loadAsrStatus}>Reset to Current</Button>
            </Space>

            {asrTestResult && (
              <Card size="small" style={{ marginTop: 16 }} title={<Space><ExperimentOutlined />ASR Test Result</Space>}>
                {asrTestResult.error ? (
                  <Tag color="red">{asrTestResult.error}</Tag>
                ) : (
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label="Transcript">{asrTestResult.text || '-'}</Descriptions.Item>
                    <Descriptions.Item label="Provider">{asrTestResult.provider || '-'}</Descriptions.Item>
                    <Descriptions.Item label="Model">{asrTestResult.model || '-'}</Descriptions.Item>
                    <Descriptions.Item label="Language">{asrTestResult.language || '-'}</Descriptions.Item>
                  </Descriptions>
                )}
              </Card>
            )}
          </Form>
        </Spin>
      </Card>

      <Divider orientation="left">Presets</Divider>
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
                    <span>{preset.name}</span>
                    {activePreset === key && (
                      <Tag color="success" icon={<CheckCircleOutlined />}>Active</Tag>
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
                    {activePreset === key ? 'Re-apply' : 'Apply'}
                  </Button>,
                ]}
              >
                <p style={{ color: '#666', marginBottom: 12 }}>{preset.description}</p>
                <Descriptions column={1} size="small" colon>
                  <Descriptions.Item label="Retrieval Top-K">{preset.retrieval_top_k}</Descriptions.Item>
                  <Descriptions.Item label="LLM Temperature">{preset.llm_temperature}</Descriptions.Item>
                  <Descriptions.Item label="LLM Max Tokens">{preset.llm_max_tokens}</Descriptions.Item>
                  <Descriptions.Item label="LLM Timeout">{(preset.llm_timeout_ms / 1000).toFixed(0)}s</Descriptions.Item>
                  <Descriptions.Item label="Tool Retries">{preset.tool_max_retries}</Descriptions.Item>
                  <Descriptions.Item label="Keyword Weight">{preset.keyword_weight}</Descriptions.Item>
                  <Descriptions.Item label="HNSW efSearch">{preset.ef_search}</Descriptions.Item>
                  <Descriptions.Item label="Reranker">
                    <Tag color={preset.reranker_enabled ? 'green' : 'default'}>
                      {preset.reranker_enabled ? 'Enabled' : 'Disabled'}
                    </Tag>
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
          ))}
        </Row>
      </Spin>

      {/* ── Current Config ───────────────────────────────── */}
      <Divider orientation="left">Current Runtime Configuration</Divider>
      <Spin spinning={loadingConfig}>
        <Card style={{ marginBottom: 24 }}>
          <Descriptions
            bordered
            column={{ xs: 1, sm: 2, md: 3 }}
            size="small"
            title={
              <Space>
                <SettingOutlined />
                <span>Active Configuration</span>
                {activePreset ? (
                  <Tag color={PRESET_COLORS[activePreset] || 'blue'}>
                    {presets[activePreset]?.name || activePreset}
                  </Tag>
                ) : (
                  <Tag color="orange">Custom</Tag>
                )}
              </Space>
            }
          >
            {Object.entries(currentConfig)
              .filter(([key]) => key !== 'active_preset' && key !== 'name' && key !== 'description')
              .map(([key, value]) => (
                <Descriptions.Item key={key} label={key}>
                  {typeof value === 'boolean' ? (
                    <Tag color={value ? 'green' : 'default'}>{value ? 'Enabled' : 'Disabled'}</Tag>
                  ) : (
                    String(value)
                  )}
                </Descriptions.Item>
              ))}
          </Descriptions>
        </Card>
      </Spin>

      {/* ── Advanced Tuning ──────────────────────────────── */}
      <Divider orientation="left">Advanced Tuning</Divider>
      <Card>
        <Form form={form} layout="vertical">
          <Row gutter={24}>
            <Col xs={24} sm={12} md={8}>
              <Form.Item name="retrieval_top_k" label="Retrieval Top-K">
                <InputNumber min={1} max={50} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={8}>
              <Form.Item name="retrieval_timeout_ms" label="Retrieval Timeout (ms)">
                <InputNumber min={1000} max={120000} step={1000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={8}>
              <Form.Item name="keyword_weight" label="Keyword Weight (BM25 in RRF)">
                <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={24}>
            <Col xs={24} sm={12} md={8}>
              <Form.Item name="ef_search" label="HNSW efSearch">
                <InputNumber min={16} max={512} step={16} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={8}>
              <Form.Item name="llm_temperature" label="LLM Temperature">
                <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={8}>
              <Form.Item name="llm_max_tokens" label="LLM Max Tokens">
                <InputNumber min={1} max={128000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={8}>
              <Form.Item name="llm_timeout_ms" label="LLM Timeout (ms)">
                <InputNumber min={1000} max={600000} step={1000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={24}>
            <Col xs={24} sm={12} md={8}>
              <Form.Item name="tool_timeout_ms" label="Tool Timeout (ms)">
                <InputNumber min={1000} max={300000} step={1000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={8}>
              <Form.Item name="tool_max_retries" label="Tool Max Retries">
                <InputNumber min={0} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={8}>
              <Form.Item name="reranker_enabled" label="Reranker Enabled" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>

          <Space>
            <Button type="primary" onClick={handleSaveConfig} loading={savingConfig}>
              Save Configuration
            </Button>
            <Button onClick={loadCurrentConfig}>
              Reset to Current
            </Button>
          </Space>
        </Form>
      </Card>
    </div>
  );
}
