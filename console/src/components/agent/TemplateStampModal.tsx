import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Alert, Button, Card, Col, Form, Input, Modal, Row, Select, Spin, Steps, Tag, Typography, message,
} from 'antd';
import { agentCapabilitiesApi, agentTemplatesApi, knowledgeApi, llmConfigApi } from '../../api';
import type { Agent, AgentCapabilities, KnowledgeSource, LLMConfig, TemplateSummary } from '../../types';

const { Text } = Typography;

interface TemplateStampModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

interface InstanceFormValues {
  name?: string;
  llm_config_id?: string;
}

function isFormValidationError(error: unknown): error is { errorFields: unknown } {
  return typeof error === 'object' && error !== null && 'errorFields' in error;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export default function TemplateStampModal({ open, onClose, onCreated }: TemplateStampModalProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [form] = Form.useForm<InstanceFormValues>();

  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [llmConfigs, setLlmConfigs] = useState<LLMConfig[]>([]);
  const [knowledgeSources, setKnowledgeSources] = useState<KnowledgeSource[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<TemplateSummary | null>(null);
  const [createdAgent, setCreatedAgent] = useState<Agent | null>(null);
  const [capabilities, setCapabilities] = useState<AgentCapabilities | null>(null);
  const [needsKnowledge, setNeedsKnowledge] = useState(false);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);

  useEffect(() => {
    if (!open) return;

    setStep(0);
    setSelectedTemplate(null);
    setCreatedAgent(null);
    setCapabilities(null);
    setNeedsKnowledge(false);
    setSelectedSourceIds([]);
    form.resetFields();

    setLoading(true);
    Promise.all([agentTemplatesApi.list(), llmConfigApi.list(), knowledgeApi.listSources()])
      .then(([templatesRes, llmRes, sourcesRes]) => {
        setTemplates(templatesRes.data);
        setLlmConfigs(llmRes.data);
        setKnowledgeSources(sourcesRes.data);
      })
      .catch((error: unknown) => {
        message.error(errorMessage(error, t('agentTemplates.loadTemplatesFailed')));
      })
      .finally(() => setLoading(false));
  }, [open, form, t]);

  const handleSelectTemplate = (template: TemplateSummary) => {
    setSelectedTemplate(template);
    form.resetFields();
    setStep(1);
  };

  const finishWithoutKnowledge = () => {
    onCreated();
    onClose();
  };

  const handleInstantiate = async () => {
    if (!selectedTemplate) return;
    try {
      const values = await form.validateFields();
      setLoading(true);
      const res = await agentTemplatesApi.instantiate(selectedTemplate.id, {
        name: values.name || undefined,
        llm_config_id: values.llm_config_id || undefined,
      });
      const agent = res.data;
      setCreatedAgent(agent);
      message.success(t('agentTemplates.agentCreated'));

      try {
        const capRes = await agentCapabilitiesApi.get(agent.id);
        const caps = capRes.data;
        const hasEmptyKnowledge = caps.knowledge.some((k) => !k.source_ids || k.source_ids.length === 0);
        if (hasEmptyKnowledge) {
          setCapabilities(caps);
          setNeedsKnowledge(true);
          setStep(2);
        } else {
          finishWithoutKnowledge();
        }
      } catch (error: unknown) {
        message.error(errorMessage(error, t('agentTemplates.loadCapabilitiesFailed')));
        finishWithoutKnowledge();
      }
    } catch (error: unknown) {
      if (isFormValidationError(error)) return;
      message.error(errorMessage(error, t('agentTemplates.instantiateFailed')));
    } finally {
      setLoading(false);
    }
  };

  const handleSaveKnowledge = async () => {
    if (!createdAgent || !capabilities) return;
    setLoading(true);
    try {
      const updatedKnowledge = capabilities.knowledge.map((k) =>
        (!k.source_ids || k.source_ids.length === 0) ? { ...k, source_ids: selectedSourceIds } : k,
      );
      await agentCapabilitiesApi.update(createdAgent.id, { ...capabilities, knowledge: updatedKnowledge });
      message.success(t('agentTemplates.knowledgeSaved'));
      finishWithoutKnowledge();
    } catch (error: unknown) {
      message.error(errorMessage(error, t('agentTemplates.saveKnowledgeFailed')));
    } finally {
      setLoading(false);
    }
  };

  const goToKnowledgePage = () => {
    finishWithoutKnowledge();
    navigate('/knowledge');
  };

  const stepItems = [
    { title: t('agentTemplates.stepSelectTemplate') },
    { title: t('agentTemplates.stepConfigure') },
    { title: t('agentTemplates.stepKnowledge') },
  ];

  return (
    <Modal
      title={t('agentTemplates.modalTitle')}
      open={open}
      onCancel={onClose}
      width={720}
      footer={null}
      destroyOnClose
    >
      <Steps current={step} size="small" items={stepItems} style={{ marginBottom: 24 }} />

      <Spin spinning={loading}>
        {step === 0 && (
          <div style={{ minHeight: 240 }}>
            <Text type="secondary">{t('agentTemplates.selectTemplateHint')}</Text>
            <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
              {templates.map((template) => (
                <Col span={12} key={template.id}>
                  <Card hoverable onClick={() => handleSelectTemplate(template)} style={{ cursor: 'pointer', height: '100%' }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>{template.name}</div>
                    <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>{template.description}</div>
                    <Tag>{t(`agentTemplates.categories.${template.category}`, template.category)}</Tag>
                  </Card>
                </Col>
              ))}
            </Row>
          </div>
        )}

        {step === 1 && selectedTemplate && (
          <Form form={form} layout="vertical" style={{ minHeight: 240 }}>
            <Alert
              type="info"
              showIcon
              message={selectedTemplate.name}
              description={selectedTemplate.description}
              style={{ marginBottom: 16 }}
            />
            <Form.Item name="name" label={t('agentTemplates.agentName')}>
              <Input placeholder={selectedTemplate.name} />
            </Form.Item>
            <Form.Item name="llm_config_id" label={t('agentTemplates.llmConfig')}>
              <Select
                placeholder={t('agentTemplates.llmConfigPlaceholder')}
                allowClear
                showSearch
                optionFilterProp="label"
                options={llmConfigs.map((c) => ({ value: c.id, label: `${c.name} (${c.provider} / ${c.model})` }))}
              />
            </Form.Item>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <Button onClick={() => setStep(0)}>{t('agentTemplates.back')}</Button>
              <Button type="primary" onClick={handleInstantiate} loading={loading}>
                {t('agentTemplates.createAgent')}
              </Button>
            </div>
          </Form>
        )}

        {step === 2 && needsKnowledge && (
          <div style={{ minHeight: 240 }}>
            <Alert
              type="info"
              showIcon
              message={t('agentTemplates.knowledgeOnboardingTitle')}
              description={t('agentTemplates.knowledgeOnboardingHint')}
              style={{ marginBottom: 16 }}
            />
            <Form layout="vertical">
              <Form.Item label={t('agentTemplates.selectKnowledgeSources')}>
                <Select
                  mode="multiple"
                  placeholder={t('agentTemplates.selectKnowledgeSources')}
                  value={selectedSourceIds}
                  onChange={setSelectedSourceIds}
                  options={knowledgeSources.map((s) => ({ value: s.id, label: `${s.name} (${s.domain})` }))}
                  optionFilterProp="label"
                />
              </Form.Item>
            </Form>
            {knowledgeSources.length === 0 && (
              <Alert
                type="warning"
                showIcon
                message={t('agentTemplates.noKnowledgeSourcesHint')}
                action={<Button size="small" onClick={goToKnowledgePage}>{t('agentTemplates.goToKnowledge')}</Button>}
                style={{ marginBottom: 16 }}
              />
            )}
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <Button onClick={finishWithoutKnowledge}>{t('agentTemplates.skip')}</Button>
              <Button type="primary" onClick={handleSaveKnowledge} loading={loading}>
                {t('agentTemplates.finish')}
              </Button>
            </div>
          </div>
        )}
      </Spin>
    </Modal>
  );
}
