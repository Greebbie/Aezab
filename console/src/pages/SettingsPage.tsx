import React, { useEffect, useState } from 'react';
import {
  Card, Button, Form, InputNumber, Switch, Space, message, Tag,
  Descriptions, Spin, Row, Col, Divider,
} from 'antd';
import {
  ThunderboltOutlined, DashboardOutlined, SafetyCertificateOutlined,
  CheckCircleOutlined, SettingOutlined,
} from '@ant-design/icons';
import { performanceApi, vectorAdminApi } from '../api';

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
  const [form] = Form.useForm();

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
