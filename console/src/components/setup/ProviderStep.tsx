import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert, Button, Card, Col, Collapse, Form, Input, Row, Space, Typography, message,
} from 'antd';
import { CheckCircleFilled } from '@ant-design/icons';
import { llmConfigApi } from '../../api';
import { PROVIDER_PRESETS, ProviderFallback } from './providerPresets';
import { classifyConnectionError } from './errorClassification';

const { Text } = Typography;

type TemplateMap = Record<string, ProviderFallback & { temperature?: number; timeout_ms?: number }>;

interface ProviderStepProps {
  onConfigCreated: (configId: string) => void;
  onNext: () => void;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export default function ProviderStep({ onConfigCreated, onNext }: ProviderStepProps) {
  const { t } = useTranslation();

  const [templates, setTemplates] = useState<TemplateMap>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [testing, setTesting] = useState(false);
  const [testStatus, setTestStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [testErrorKind, setTestErrorKind] = useState<'authError' | 'networkError' | 'otherError' | null>(null);
  const [rawError, setRawError] = useState('');
  const [creating, setCreating] = useState(false);
  const [createdConfigId, setCreatedConfigId] = useState<string | null>(null);

  useEffect(() => {
    llmConfigApi.getTemplates()
      .then((res) => setTemplates(res.data as unknown as TemplateMap))
      .catch(() => {
        // Fall back to the constants baked into providerPresets.ts.
      });
  }, []);

  const selectedPreset = PROVIDER_PRESETS.find((p) => p.id === selectedId) ?? null;

  const resolvedPreset = (): ProviderFallback => {
    if (!selectedPreset) return { provider: 'openai_compatible', base_url: '', model: '' };
    const remote = templates[selectedPreset.templateKey];
    return remote ?? selectedPreset.fallback;
  };

  const markDirty = () => {
    if (testStatus !== 'idle') {
      setTestStatus('idle');
      setCreatedConfigId(null);
    }
  };

  const handleSelectProvider = (id: string) => {
    const preset = PROVIDER_PRESETS.find((p) => p.id === id);
    if (!preset) return;
    const remote = templates[preset.templateKey];
    const values = remote ?? preset.fallback;
    setSelectedId(id);
    setApiKey('');
    setModel(values.model);
    setBaseUrl(values.base_url);
    setTestStatus('idle');
    setTestErrorKind(null);
    setRawError('');
    setCreatedConfigId(null);
  };

  const handleTest = async () => {
    if (!selectedPreset) return;
    const preset = resolvedPreset();
    if (!baseUrl.trim() || !model.trim()) {
      message.warning(t('setup.step1.fillRequired'));
      return;
    }
    setTesting(true);
    setTestStatus('idle');
    setTestErrorKind(null);
    setRawError('');
    try {
      const res = await llmConfigApi.test({
        base_url: baseUrl.trim(),
        api_key: apiKey,
        model: model.trim(),
        temperature: 0.3,
        max_tokens: 256,
        timeout_ms: 30000,
      });
      const data = res.data as { success: boolean; error?: string };
      if (data.success) {
        setTestStatus('success');
      } else {
        setTestStatus('error');
        const err = data.error || '';
        setTestErrorKind(classifyConnectionError(err));
        setRawError(err);
      }
    } catch (error: unknown) {
      const msg = errorMessage(error, t('setup.step1.testFailedGeneric'));
      setTestStatus('error');
      setTestErrorKind(classifyConnectionError(msg));
      setRawError(msg);
    } finally {
      setTesting(false);
      void preset;
    }
  };

  const createConfig = async (nameSuffix = ''): Promise<string> => {
    if (!selectedPreset) throw new Error('No provider selected');
    const preset = resolvedPreset();
    const baseName = t(`setup.providers.${selectedPreset.id}.name`);
    const res = await llmConfigApi.create({
      name: `${baseName}${nameSuffix}`,
      provider: preset.provider,
      base_url: baseUrl.trim(),
      api_key: apiKey,
      model: model.trim(),
      is_default: true,
    });
    return res.data.id;
  };

  const handleNext = async () => {
    if (testStatus !== 'success') return;
    if (createdConfigId) {
      onConfigCreated(createdConfigId);
      onNext();
      return;
    }
    setCreating(true);
    try {
      let configId: string;
      try {
        configId = await createConfig();
      } catch (error: unknown) {
        const msg = errorMessage(error, '');
        if (/already exists|duplicate|409/i.test(msg)) {
          configId = await createConfig(`-${Date.now().toString(36)}`);
        } else {
          throw error;
        }
      }
      setCreatedConfigId(configId);
      onConfigCreated(configId);
      onNext();
    } catch (error: unknown) {
      message.error(errorMessage(error, t('setup.step1.createFailed')));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div>
      <Typography.Title level={5}>{t('setup.step1.title')}</Typography.Title>
      <Text type="secondary">{t('setup.step1.hint')}</Text>

      <Row gutter={[12, 12]} style={{ marginTop: 16, marginBottom: 16 }}>
        {PROVIDER_PRESETS.map((preset) => (
          <Col xs={12} md={8} key={preset.id}>
            <Card
              hoverable
              onClick={() => handleSelectProvider(preset.id)}
              style={{
                cursor: 'pointer',
                height: '100%',
                borderColor: selectedId === preset.id ? '#1677ff' : undefined,
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                {t(`setup.providers.${preset.id}.name`)}
                {selectedId === preset.id && (
                  <CheckCircleFilled style={{ color: '#1677ff', marginLeft: 8 }} />
                )}
              </div>
              <div style={{ fontSize: 12, color: '#999' }}>
                {t(`setup.providers.${preset.id}.description`)}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {selectedPreset && (
        <Form layout="vertical">
          {selectedPreset.editableBaseUrl ? (
            <Form.Item label={t('setup.fields.baseUrl')} help={t('setup.fields.baseUrlHelp')}>
              <Input
                value={baseUrl}
                onChange={(e) => { setBaseUrl(e.target.value); markDirty(); }}
                placeholder="http://localhost:11434/v1"
              />
            </Form.Item>
          ) : (
            // Providers with a fixed default base URL (DashScope, Zhipu,
            // MiniMax, OpenAI) still need an escape hatch for self-hosted
            // relays/proxies — collapsed by default so it doesn't clutter
            // the common "just paste an API key" path.
            <Collapse
              ghost
              size="small"
              style={{ marginBottom: 16 }}
              items={[{
                key: 'advanced',
                label: t('setup.step1.advancedSettings'),
                children: (
                  <Form.Item
                    label={t('setup.fields.baseUrl')}
                    help={t('setup.step1.advancedSettingsHint')}
                  >
                    <Input
                      value={baseUrl}
                      onChange={(e) => { setBaseUrl(e.target.value); markDirty(); }}
                    />
                  </Form.Item>
                ),
              }]}
            />
          )}

          {selectedPreset.needsApiKey && (
            <Form.Item label={t('setup.fields.apiKey')} help={t('setup.fields.apiKeyHelp')}>
              <Input.Password
                value={apiKey}
                onChange={(e) => { setApiKey(e.target.value); markDirty(); }}
                placeholder={t('setup.fields.apiKeyPlaceholder')}
              />
            </Form.Item>
          )}

          <Form.Item label={t('setup.fields.model')} help={t('setup.fields.modelHelp')}>
            <Input
              value={model}
              onChange={(e) => { setModel(e.target.value); markDirty(); }}
            />
          </Form.Item>

          <Space style={{ marginBottom: 16 }}>
            <Button onClick={handleTest} loading={testing}>
              {t('setup.step1.testButton')}
            </Button>
          </Space>

          {testStatus === 'success' && (
            <Alert
              type="success"
              showIcon
              message={t('setup.step1.testSuccess')}
              style={{ marginBottom: 16 }}
            />
          )}

          {testStatus === 'error' && testErrorKind && (
            <div style={{ marginBottom: 16 }}>
              <Alert type="error" showIcon message={t(`setup.step1.testErrors.${testErrorKind}`)} />
              <Collapse
                ghost
                size="small"
                items={[{ key: '1', label: t('setup.step1.showRawError'), children: <Text code>{rawError}</Text> }]}
              />
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button
              type="primary"
              disabled={testStatus !== 'success'}
              loading={creating}
              onClick={handleNext}
            >
              {t('common.next')}
            </Button>
          </div>
        </Form>
      )}
    </div>
  );
}
