import { useEffect, useState } from 'react';
import {
  Modal, Form, Input, InputNumber, Switch, Space, Card, Row, Col,
  Collapse, Descriptions, Typography, Button, message,
} from 'antd';
import { CheckCircleFilled, ExperimentOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { llmConfigApi } from '../../api';
import { friendlyError } from '../../utils/friendlyError';
import type { LLMConfig, LLMConfigCreate, LLMConfigUpdate } from '../../types';
import { PROVIDER_PRESETS, ProviderFallback } from '../setup/providerPresets';
import { HelpLabel } from '../shared';

/**
 * `LLMConfig` (console/src/types/llm.ts) predates the `top_p` column added to
 * the backend schema. Widening it locally here avoids touching the shared
 * type file, which is out of scope for this component.
 */
export interface LLMConfigWithTopP extends LLMConfig {
  top_p?: number;
}

interface LLMConfigFormValues {
  name: string;
  provider: string;
  base_url: string;
  api_key?: string;
  model: string;
  temperature: number;
  top_p: number;
  max_tokens: number;
  timeout_ms: number;
  is_default: boolean;
}

interface LLMConfigTestResult {
  success: boolean;
  content?: string;
  latency_ms?: number;
  error?: string;
}

type TemplateMap = Record<string, ProviderFallback & {
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  timeout_ms?: number;
}>;

const DEFAULT_FORM_VALUES: Pick<LLMConfigFormValues, 'provider' | 'temperature' | 'top_p' | 'max_tokens' | 'timeout_ms' | 'is_default'> = {
  provider: 'openai_compatible',
  temperature: 0.3,
  top_p: 1.0,
  max_tokens: 2048,
  timeout_ms: 60000,
  is_default: false,
};

function isFormValidationError(err: unknown): boolean {
  return typeof err === 'object' && err !== null && 'errorFields' in err;
}

function keyLooksUnset(value?: string): boolean {
  return !value || value.trim() === '' || value === '********';
}

function normalizeUrl(url?: string): string {
  return (url || '').trim().replace(/\/+$/, '');
}

interface LLMConfigModalProps {
  open: boolean;
  editing: LLMConfigWithTopP | null;
  onClose: () => void;
  onSaved: () => void;
}

export default function LLMConfigModal({ open, editing, onClose, onSaved }: LLMConfigModalProps) {
  const { t } = useTranslation();
  const [form] = Form.useForm<LLMConfigFormValues>();
  const [templates, setTemplates] = useState<TemplateMap>({});
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<LLMConfigTestResult | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    llmConfigApi.getTemplates()
      .then((res) => setTemplates(res.data as unknown as TemplateMap))
      .catch(() => {
        // Fall back silently to the constants baked into providerPresets.ts.
      });
  }, []);

  const matchPresetId = (record: LLMConfigWithTopP): string => {
    for (const preset of PROVIDER_PRESETS) {
      const resolved = templates[preset.templateKey] ?? preset.fallback;
      if (resolved.base_url && normalizeUrl(resolved.base_url) === normalizeUrl(record.base_url)) {
        return preset.id;
      }
    }
    return record.provider === 'local' ? 'ollama' : 'custom';
  };

  useEffect(() => {
    if (!open) return;
    setTestResult(null);
    if (editing) {
      form.setFieldsValue({
        name: editing.name,
        provider: editing.provider,
        base_url: editing.base_url,
        api_key: '',
        model: editing.model,
        temperature: editing.temperature,
        top_p: editing.top_p ?? 1.0,
        max_tokens: editing.max_tokens,
        timeout_ms: editing.timeout_ms,
        is_default: editing.is_default,
      });
      setSelectedPresetId(matchPresetId(editing));
    } else {
      form.resetFields();
      form.setFieldsValue(DEFAULT_FORM_VALUES);
      setSelectedPresetId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editing]);

  const selectedPreset = PROVIDER_PRESETS.find((p) => p.id === selectedPresetId) ?? null;
  const showApiKeyInMain = !selectedPreset || selectedPreset.needsApiKey;
  const showBaseUrlInMain = !selectedPreset || selectedPreset.editableBaseUrl;
  const showBaseUrlInAdvanced = !showBaseUrlInMain;

  const handleSelectPreset = (id: string) => {
    const preset = PROVIDER_PRESETS.find((p) => p.id === id);
    if (!preset) return;
    const resolved = templates[preset.templateKey] ?? preset.fallback;
    form.setFieldsValue({
      provider: resolved.provider,
      base_url: resolved.base_url,
      model: resolved.model,
      api_key: '',
      temperature: resolved.temperature ?? form.getFieldValue('temperature') ?? DEFAULT_FORM_VALUES.temperature,
      top_p: resolved.top_p ?? form.getFieldValue('top_p') ?? DEFAULT_FORM_VALUES.top_p,
      timeout_ms: resolved.timeout_ms ?? form.getFieldValue('timeout_ms') ?? DEFAULT_FORM_VALUES.timeout_ms,
    });
    setSelectedPresetId(id);
    setTestResult(null);
  };

  const handleTest = async () => {
    try {
      await form.validateFields(['base_url', 'model']);
    } catch (err: unknown) {
      if (isFormValidationError(err)) {
        message.warning(t('llmConfigs.testFillRequired'));
        return;
      }
      message.error(friendlyError(err, t));
      return;
    }

    const values = form.getFieldsValue();
    const needsKey = selectedPreset ? selectedPreset.needsApiKey : values.provider !== 'local';
    if (needsKey && keyLooksUnset(values.api_key)) {
      message.warning(t('llmConfigs.testNeedsKey'));
      return;
    }

    setTesting(true);
    setTestResult(null);
    try {
      const res = await llmConfigApi.test({
        base_url: values.base_url,
        api_key: values.api_key || '',
        model: values.model,
        temperature: values.temperature ?? DEFAULT_FORM_VALUES.temperature,
        max_tokens: 256,
        timeout_ms: values.timeout_ms ?? DEFAULT_FORM_VALUES.timeout_ms,
      });
      const result = res.data as LLMConfigTestResult;
      setTestResult(result);
      if (result.success) {
        message.success(t('llmConfigs.testSuccess'));
      } else {
        message.error(t('llmConfigs.testFailed'));
      }
    } catch (err: unknown) {
      message.error(friendlyError(err, t));
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    let values: LLMConfigFormValues;
    try {
      values = await form.validateFields();
    } catch (err: unknown) {
      if (isFormValidationError(err)) return;
      message.error(friendlyError(err, t));
      return;
    }

    setSaving(true);
    try {
      if (editing) {
        const payload: Partial<LLMConfigFormValues> = { ...values };
        if (keyLooksUnset(payload.api_key)) delete payload.api_key;
        await llmConfigApi.update(editing.id, payload as LLMConfigUpdate);
        message.success(t('llmConfigs.updateSuccess'));
      } else {
        await llmConfigApi.create(values as LLMConfigCreate);
        message.success(t('llmConfigs.createSuccess'));
      }
      onSaved();
    } catch (err: unknown) {
      message.error(friendlyError(err, t));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={editing ? t('llmConfigs.edit') : t('llmConfigs.create')}
      open={open}
      onOk={handleSave}
      onCancel={onClose}
      okText={t('common.save')}
      cancelText={t('common.cancel')}
      confirmLoading={saving}
      width={720}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary">{t('llmConfigs.presetHint')}</Typography.Paragraph>
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        {PROVIDER_PRESETS.map((preset) => (
          <Col xs={12} md={8} key={preset.id}>
            <Card
              hoverable
              size="small"
              onClick={() => handleSelectPreset(preset.id)}
              style={{
                cursor: 'pointer',
                height: '100%',
                borderColor: selectedPresetId === preset.id ? '#1677ff' : undefined,
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
                {t(`setup.providers.${preset.id}.name`)}
                {selectedPresetId === preset.id && (
                  <CheckCircleFilled style={{ color: '#1677ff', marginLeft: 6 }} />
                )}
              </div>
              <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', lineHeight: 1.4 }}>
                {t(`setup.providers.${preset.id}.description`)}
              </Typography.Text>
            </Card>
          </Col>
        ))}
      </Row>

      <Form form={form} layout="vertical" initialValues={DEFAULT_FORM_VALUES}>
        <Form.Item name="provider" hidden>
          <Input />
        </Form.Item>

        <Form.Item name="name" label={t('common.name')} rules={[{ required: true, message: t('llmConfigs.nameRequired') }]}>
          <Input placeholder={t('llmConfigs.namePlaceholder')} />
        </Form.Item>

        {showApiKeyInMain && (
          <Form.Item
            name="api_key"
            label={<HelpLabel label={t('llmConfigs.apiKey')} help={t('llmConfigs.apiKeyHelp')} />}
          >
            <Input.Password
              placeholder={editing ? t('llmConfigs.apiKeySavedPlaceholder') : t('llmConfigs.apiKeyPlaceholder')}
              autoComplete="new-password"
            />
          </Form.Item>
        )}

        {showBaseUrlInMain && (
          <Form.Item
            name="base_url"
            label={<HelpLabel label={t('llmConfigs.baseUrl')} help={t('llmConfigs.baseUrlHelp')} />}
            rules={[{ required: true, message: t('llmConfigs.baseUrlRequired') }]}
          >
            <Input placeholder="http://localhost:11434/v1" />
          </Form.Item>
        )}

        <Form.Item
          name="model"
          label={<HelpLabel label={t('llmConfigs.model')} help={t('llmConfigs.modelHelp')} />}
          rules={[{ required: true, message: t('llmConfigs.modelRequired') }]}
        >
          <Input placeholder="gpt-4o / qwen-flash / glm-4" />
        </Form.Item>

        <Space style={{ marginBottom: 16 }}>
          <Button icon={<ExperimentOutlined />} loading={testing} onClick={handleTest}>
            {t('llmConfigs.testConfig')}
          </Button>
        </Space>

        {testResult && (
          <Card
            size="small"
            style={{ marginBottom: 16 }}
            title={testResult.success ? t('llmConfigs.testPassedTitle') : t('llmConfigs.testFailedTitle')}
            styles={{ header: { background: testResult.success ? '#f6ffed' : '#fff2f0' } }}
          >
            <Descriptions column={1} size="small">
              {testResult.success ? (
                <>
                  <Descriptions.Item label={t('llmConfigs.testResponse')}>{testResult.content}</Descriptions.Item>
                  <Descriptions.Item label={t('llmConfigs.testLatency')}>{testResult.latency_ms?.toFixed(0)}ms</Descriptions.Item>
                </>
              ) : (
                <Descriptions.Item label={t('llmConfigs.testError')}>{testResult.error}</Descriptions.Item>
              )}
            </Descriptions>
          </Card>
        )}

        <Collapse
          style={{ marginBottom: 16 }}
          items={[{
            key: 'advanced',
            label: t('llmConfigs.advancedSettings'),
            children: (
              <>
                {showBaseUrlInAdvanced && (
                  <Form.Item
                    name="base_url"
                    label={<HelpLabel label={t('llmConfigs.baseUrl')} help={t('llmConfigs.baseUrlHelp')} />}
                    rules={[{ required: true, message: t('llmConfigs.baseUrlRequired') }]}
                  >
                    <Input placeholder="https://api.openai.com/v1" />
                  </Form.Item>
                )}
                <Space size="large" wrap>
                  <Form.Item name="temperature" label={<HelpLabel label={t('llmConfigs.temperature')} help={t('llmConfigs.temperatureHelp')} />}>
                    <InputNumber min={0} max={2} step={0.1} style={{ width: 140 }} />
                  </Form.Item>
                  <Form.Item name="top_p" label={<HelpLabel label={t('llmConfigs.topP')} help={t('llmConfigs.topPHelp')} />}>
                    <InputNumber min={0} max={1} step={0.05} style={{ width: 140 }} />
                  </Form.Item>
                  <Form.Item name="max_tokens" label={<HelpLabel label={t('llmConfigs.maxTokens')} help={t('llmConfigs.maxTokensHelp')} />}>
                    <InputNumber min={1} max={128000} style={{ width: 150 }} />
                  </Form.Item>
                  <Form.Item name="timeout_ms" label={<HelpLabel label={t('llmConfigs.timeout')} help={t('llmConfigs.timeoutHelp')} />}>
                    <InputNumber min={1000} max={600000} step={1000} style={{ width: 150 }} />
                  </Form.Item>
                </Space>
              </>
            ),
          }]}
        />

        <Form.Item name="is_default" label={t('llmConfigs.isDefault')} valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
    </Modal>
  );
}
