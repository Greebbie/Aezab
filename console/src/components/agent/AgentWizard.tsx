import React, { useState } from 'react';
import { Alert, Modal, Steps, Button, Form, Input, Select, Space, Spin, message } from 'antd';
import {
  UserOutlined,
  RobotOutlined,
  DatabaseOutlined,
  ToolOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { LLMConfig, KnowledgeSource, Workflow, Tool } from '../../types';
import { agentApi, agentCapabilitiesApi, llmConfigApi, knowledgeApi, workflowApi, toolApi } from '../../api';

const { TextArea } = Input;

interface AgentWizardProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  onSwitchToTemplate: () => void;
}

export default function AgentWizard({ open, onClose, onCreated, onSwitchToTemplate }: AgentWizardProps) {
  const { t } = useTranslation();
  const [current, setCurrent] = useState(0);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [createdAgentId, setCreatedAgentId] = useState<string | null>(null);

  // Data for selection steps
  const [llmConfigs, setLlmConfigs] = useState<LLMConfig[]>([]);
  const [knowledgeSources, setKnowledgeSources] = useState<KnowledgeSource[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);

  // Load data on open
  React.useEffect(() => {
    if (open) {
      setCurrent(0);
      setCreatedAgentId(null);
      form.resetFields();
      // Load reference data
      llmConfigApi.list().then((r) => setLlmConfigs(r.data)).catch(() => {});
      knowledgeApi.listSources().then((r) => setKnowledgeSources(r.data)).catch(() => {});
      workflowApi.list().then((r) => setWorkflows(r.data)).catch(() => {});
      toolApi.list().then((r) => setTools(r.data)).catch(() => {});
    }
  }, [open, form]);

  const handleNext = async () => {
    setLoading(true);
    try {
      if (current === 0) {
        await form.validateFields(['name', 'description', 'system_prompt']);
      }
      if (current === 1) {
        await form.validateFields(['llm_config_id']);
      }

      // Create agent at step 0 (after basic info validated)
      if (current === 0 && !createdAgentId) {
        const values = form.getFieldsValue();
        const resp = await agentApi.create({
          name: values.name,
          description: values.description || '',
          system_prompt: values.system_prompt || '',
        });
        setCreatedAgentId(resp.data.id);
      }

      // Save LLM config at step 1->2 transition
      if (current === 1 && createdAgentId) {
        const llmConfigId = form.getFieldValue('llm_config_id');
        if (llmConfigId) {
          await agentApi.update(createdAgentId, { llm_config_id: llmConfigId });
        }
      }

      // Save capabilities at step 3 (tools & workflows)
      if (current === 3 && createdAgentId) {
        const selectedKnowledge: string[] = form.getFieldValue('knowledge_source_ids') || [];
        const selectedWorkflows: string[] = form.getFieldValue('workflow_ids') || [];
        const selectedTools: string[] = form.getFieldValue('tool_ids') || [];

        await agentCapabilitiesApi.update(createdAgentId, {
          knowledge: selectedKnowledge.map((id) => ({
            source_ids: [id],
            domain: 'default',
            keywords: [],
            description: '',
          })),
          workflows: selectedWorkflows.map((id) => ({
            workflow_id: id,
            keywords: [],
            description: '',
          })),
          tools: selectedTools.length > 0
            ? [{ tool_ids: selectedTools, keywords: [], description: '' }]
            : [],
        });
      }

      // Only advance step if all API calls above succeeded
      setCurrent(current + 1);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Operation failed';
      message.error(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleFinish = () => {
    message.success('Agent created and configured successfully!');
    onCreated();
    onClose();
  };

  const steps = [
    {
      title: 'Basic Info',
      icon: <UserOutlined />,
      content: (
        <div>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message={(
              <span>
                {t('agents.wizard.quickStartPrefix')}{' '}
                <a onClick={onSwitchToTemplate}>{t('agents.wizard.tryTemplateLink')}</a>
              </span>
            )}
          />
          <Form.Item label="Agent Name" name="name" rules={[{ required: true, message: 'Please enter a name' }]}>
            <Input placeholder="My Agent" />
          </Form.Item>
          <Form.Item label="Description" name="description">
            <TextArea rows={2} placeholder="What does this agent do?" />
          </Form.Item>
          <Form.Item label="System Prompt" name="system_prompt">
            <TextArea rows={5} placeholder="You are a helpful assistant..." />
          </Form.Item>
        </div>
      ),
    },
    {
      title: 'LLM Config',
      icon: <RobotOutlined />,
      content: (
        <div>
          <Form.Item label="Language Model Configuration" name="llm_config_id">
            <Select
              placeholder="Select an LLM configuration"
              allowClear
              options={llmConfigs.map((c) => ({
                value: c.id,
                label: `${c.name} (${c.provider} / ${c.model})`,
              }))}
            />
          </Form.Item>
          <div style={{ color: '#999', fontSize: 12 }}>
            No configs? Create one in the LLM Configs page first, or skip this step to use the system default.
          </div>
        </div>
      ),
    },
    {
      title: 'Knowledge',
      icon: <DatabaseOutlined />,
      content: (
        <div>
          <Form.Item label="Knowledge Sources" name="knowledge_source_ids">
            <Select
              mode="multiple"
              placeholder="Select knowledge sources (optional)"
              options={knowledgeSources.map((s) => ({
                value: s.id,
                label: `${s.name} (${s.source_type})`,
              }))}
            />
          </Form.Item>
          <div style={{ color: '#999', fontSize: 12 }}>
            Optional: bind knowledge sources for RAG. Skip if this agent does not need knowledge retrieval.
          </div>
        </div>
      ),
    },
    {
      title: 'Tools & Workflows',
      icon: <ToolOutlined />,
      content: (
        <div>
          <Form.Item label="Workflows" name="workflow_ids">
            <Select
              mode="multiple"
              placeholder="Select workflows (optional)"
              options={workflows.map((w) => ({
                value: w.id,
                label: w.name,
              }))}
            />
          </Form.Item>
          <Form.Item label="Tools" name="tool_ids">
            <Select
              mode="multiple"
              placeholder="Select tools (optional)"
              options={tools.map((t) => ({
                value: t.id,
                label: `${t.name} (${t.category})`,
              }))}
            />
          </Form.Item>
        </div>
      ),
    },
    {
      title: 'Done',
      icon: <PlayCircleOutlined />,
      content: (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>
          <PlayCircleOutlined style={{ fontSize: 48, color: '#52c41a', marginBottom: 16 }} />
          <h3>Agent Created Successfully!</h3>
          <p style={{ color: '#999' }}>
            Your agent &ldquo;{form.getFieldValue('name')}&rdquo; is ready. You can test it in the Playground.
          </p>
        </div>
      ),
    },
  ];

  return (
    <Modal
      title="Create Agent"
      open={open}
      onCancel={onClose}
      width={700}
      footer={null}
      destroyOnClose
    >
      <Steps current={current} size="small" style={{ marginBottom: 24 }}>
        {steps.map((s) => (
          <Steps.Step key={s.title} title={s.title} icon={s.icon} />
        ))}
      </Steps>

      <Spin spinning={loading}>
        <Form form={form} layout="vertical" style={{ minHeight: 300 }}>
          {steps[current].content}
        </Form>
      </Spin>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16 }}>
        <Button disabled={current === 0} onClick={() => setCurrent(current - 1)}>
          Previous
        </Button>
        <Space>
          {current < steps.length - 1 ? (
            <Button type="primary" onClick={handleNext} loading={loading}>
              {current === 0 ? 'Create & Next' : 'Next'}
            </Button>
          ) : (
            <Button type="primary" onClick={handleFinish}>
              Go to Playground
            </Button>
          )}
        </Space>
      </div>
    </Modal>
  );
}
