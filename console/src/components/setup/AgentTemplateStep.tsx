import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Card, Col, Input, Row, Space, Spin, Typography, message } from 'antd';
import { agentTemplatesApi } from '../../api';
import type { Agent, TemplateSummary } from '../../types';

const { Text } = Typography;

interface AgentTemplateStepProps {
  llmConfigId: string | null;
  onAgentCreated: (agent: Agent) => void;
  onSkip: () => void;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export default function AgentTemplateStep({ llmConfigId, onAgentCreated, onSkip }: AgentTemplateStepProps) {
  const { t } = useTranslation();

  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [customName, setCustomName] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    setLoading(true);
    agentTemplatesApi.list()
      .then((res) => setTemplates(res.data))
      .catch((error: unknown) => {
        message.error(errorMessage(error, t('setup.step2.loadFailed')));
      })
      .finally(() => setLoading(false));
  }, [t]);

  const handleCreate = async () => {
    if (!selectedId) return;
    setCreating(true);
    try {
      const res = await agentTemplatesApi.instantiate(selectedId, {
        name: customName.trim() || undefined,
        llm_config_id: llmConfigId || undefined,
      });
      onAgentCreated(res.data);
    } catch (error: unknown) {
      message.error(errorMessage(error, t('setup.step2.createFailed')));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div>
      <Typography.Title level={5}>{t('setup.step2.title')}</Typography.Title>
      <Text type="secondary">{t('setup.step2.hint')}</Text>

      <Spin spinning={loading}>
        <Row gutter={[12, 12]} style={{ marginTop: 16, marginBottom: 16, minHeight: 120 }}>
          {templates.map((template) => (
            <Col xs={24} md={8} key={template.id}>
              <Card
                hoverable
                onClick={() => setSelectedId(template.id)}
                style={{
                  cursor: 'pointer',
                  height: '100%',
                  borderColor: selectedId === template.id ? '#1677ff' : undefined,
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{template.name}</div>
                <div style={{ fontSize: 12, color: '#999' }}>{template.description}</div>
              </Card>
            </Col>
          ))}
        </Row>
      </Spin>

      {selectedId && (
        <Input
          value={customName}
          onChange={(e) => setCustomName(e.target.value)}
          placeholder={t('setup.step2.namePlaceholder')}
          style={{ marginBottom: 16 }}
        />
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <Button type="link" onClick={onSkip}>
          {t('setup.step2.skip')}
        </Button>
        <Space>
          <Button type="primary" disabled={!selectedId} loading={creating} onClick={handleCreate}>
            {t('setup.step2.createButton')}
          </Button>
        </Space>
      </div>
    </div>
  );
}
