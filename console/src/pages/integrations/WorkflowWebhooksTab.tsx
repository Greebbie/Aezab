import { useMemo, useState } from 'react';
import { LinkOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Card, Form, Input, Modal, Select, Table, Tag, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { workflowApi } from '../../api';
import type { Workflow, WorkflowStep } from '../../types';
import type { IntegrationCopy } from './copy';

const { TextArea } = Input;
const CONFIGURE_WORKFLOW_WEBHOOK_LABEL = 'Configure Workflow Webhook';

interface Props {
  workflows: Workflow[];
  copy: IntegrationCopy;
  onChanged: () => void | Promise<void>;
}

interface WebhookFormValues {
  workflow_id: string;
  step_id?: string;
  webhook_url: string;
  webhook_method?: string;
  webhook_headers_json?: string;
}

interface WebhookRow {
  id: string;
  workflow: Workflow;
  step: WorkflowStep;
  enabled: boolean;
  url: string;
  method: string;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseHeaders(value: string | undefined): Record<string, string> {
  if (!value?.trim()) return {};
  const parsed = JSON.parse(value) as unknown;
  if (!isPlainObject(parsed)) {
    throw new SyntaxError('Headers must be a JSON object');
  }

  for (const headerValue of Object.values(parsed)) {
    if (typeof headerValue !== 'string') {
      throw new SyntaxError('Header values must be strings');
    }
  }

  return parsed as Record<string, string>;
}

function nextOrder(workflow: Workflow) {
  const orders = (workflow.steps || []).map((step) => step.order || 0);
  return orders.length ? Math.max(...orders) + 1 : 0;
}

export default function WorkflowWebhooksTab({ workflows, copy, onChanged }: Props) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState('');
  const [form] = Form.useForm<WebhookFormValues>();

  const selectedWorkflow = workflows.find((workflow) => workflow.id === selectedWorkflowId);
  const completeSteps = useMemo(
    () => (selectedWorkflow?.steps || []).filter((step) => step.step_type === 'complete'),
    [selectedWorkflow],
  );

  const webhookRows = useMemo(
    () =>
      workflows.flatMap((workflow) =>
        (workflow.steps || [])
          .filter((step) => step.step_type === 'complete')
          .map((step) => ({
            id: `${workflow.id}:${step.id}`,
            workflow,
            step,
            enabled: Boolean(step.tool_config?.webhook_enabled),
            url: String(step.tool_config?.webhook_url || ''),
            method: String(step.tool_config?.webhook_method || 'POST'),
          })),
      ),
    [workflows],
  );

  const closeModal = () => {
    setOpen(false);
    setSelectedWorkflowId('');
    form.resetFields();
  };

  const openNewWebhook = () => {
    setSelectedWorkflowId('');
    form.resetFields();
    form.setFieldsValue({
      webhook_method: 'POST',
      webhook_headers_json: '{}',
    });
    setOpen(true);
  };

  const openForStep = (workflow: Workflow, step: WorkflowStep) => {
    setSelectedWorkflowId(workflow.id);
    form.setFieldsValue({
      workflow_id: workflow.id,
      step_id: step.id,
      webhook_url: String(step.tool_config?.webhook_url || ''),
      webhook_method: String(step.tool_config?.webhook_method || 'POST'),
      webhook_headers_json: step.tool_config?.webhook_headers
        ? JSON.stringify(step.tool_config.webhook_headers, null, 2)
        : '{}',
    });
    setOpen(true);
  };

  const save = async () => {
    try {
      const values = await form.validateFields();
      const workflow = workflows.find((item) => item.id === values.workflow_id);
      if (!workflow) return;

      const toolConfig = {
        webhook_enabled: true,
        webhook_url: values.webhook_url,
        webhook_method: values.webhook_method || 'POST',
        webhook_headers: parseHeaders(values.webhook_headers_json),
      };

      setSaving(true);
      if (values.step_id) {
        const step = (workflow.steps || []).find((item) => item.id === values.step_id);
        if (!step) return;

        await workflowApi.updateStep(workflow.id, step.id, {
          name: step.name,
          order: step.order,
          step_type: step.step_type,
          prompt_template: step.prompt_template,
          fields: step.fields,
          validation_rules: step.validation_rules,
          tool_id: step.tool_id,
          tool_config: { ...(step.tool_config || {}), ...toolConfig },
          on_failure: step.on_failure,
          max_retries: step.max_retries,
          fallback_step_id: step.fallback_step_id,
          next_step_rules: step.next_step_rules,
          requires_human_confirm: step.requires_human_confirm,
          risk_level: step.risk_level,
        } as Parameters<typeof workflowApi.updateStep>[2]);
      } else {
        await workflowApi.addStep(workflow.id, {
          name: 'Send completion webhook',
          order: nextOrder(workflow),
          step_type: 'complete',
          prompt_template: 'Workflow completed.',
          tool_config: toolConfig,
          on_failure: 'retry',
          max_retries: 2,
          risk_level: 'info',
        });
      }

      message.success(copy.messages.webhookSaved);
      closeModal();
      await onChanged();
    } catch (error) {
      if (typeof error === 'object' && error && 'errorFields' in error) return;
      if (error instanceof SyntaxError) {
        message.error(copy.messages.invalidHeaders);
      } else {
        message.error(error instanceof Error ? error.message : copy.messages.invalidHeaders);
      }
    } finally {
      setSaving(false);
    }
  };

  const columns: ColumnsType<WebhookRow> = [
    {
      title: copy.fields.workflow,
      key: 'workflow',
      ellipsis: true,
      render: (_, row) => row.workflow.name,
    },
    {
      title: copy.fields.completeStep,
      key: 'step',
      ellipsis: true,
      render: (_, row) => row.step.name,
    },
    { title: copy.fields.method, dataIndex: 'method', key: 'method', width: 90 },
    { title: copy.fields.webhookUrl, dataIndex: 'url', key: 'url', ellipsis: true },
    {
      title: copy.fields.status,
      dataIndex: 'enabled',
      key: 'enabled',
      width: 120,
      render: (enabled: boolean) => <Tag color={enabled ? 'green' : 'default'}>{enabled ? 'Webhook' : 'None'}</Tag>,
    },
    {
      title: '',
      key: 'actions',
      width: 190,
      render: (_, row) => (
        <Button size="small" onClick={() => openForStep(row.workflow, row.step)}>
          {copy.actions.configureWebhook || CONFIGURE_WORKFLOW_WEBHOOK_LABEL}
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Card
        title={copy.actions.configureWebhook || CONFIGURE_WORKFLOW_WEBHOOK_LABEL}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openNewWebhook}>
            {copy.actions.configureWebhook || CONFIGURE_WORKFLOW_WEBHOOK_LABEL}
          </Button>
        }
      >
        <Table
          size="small"
          dataSource={webhookRows}
          columns={columns}
          rowKey="id"
          pagination={{ pageSize: 6 }}
          locale={{ emptyText: copy.empty.noWorkflows }}
        />
      </Card>

      <Modal
        title={copy.actions.configureWebhook || CONFIGURE_WORKFLOW_WEBHOOK_LABEL}
        open={open}
        onOk={save}
        okText={copy.actions.saveWebhook}
        confirmLoading={saving}
        onCancel={closeModal}
        width={720}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ webhook_method: 'POST', webhook_headers_json: '{}' }}
        >
          <Form.Item name="workflow_id" label={copy.fields.workflowSelect} rules={[{ required: true }]}>
            <Select
              placeholder={copy.fields.workflowSelect}
              onChange={(workflowId) => {
                setSelectedWorkflowId(workflowId);
                form.setFieldValue('step_id', undefined);
              }}
              options={workflows.map((workflow) => ({
                value: workflow.id,
                label: `${workflow.name} (${workflow.id})`,
              }))}
            />
          </Form.Item>
          <Form.Item name="step_id" label={copy.fields.completeStep}>
            <Select
              allowClear
              placeholder="Leave empty to add a new complete step"
              options={completeSteps.map((step) => ({
                value: step.id,
                label: `${step.name} (${step.id})`,
              }))}
            />
          </Form.Item>
          <Form.Item name="webhook_url" label={copy.fields.webhookUrl} rules={[{ required: true, type: 'url' }]}>
            <Input prefix={<LinkOutlined />} placeholder="https://customer-system.example.com/webhooks/hlab" />
          </Form.Item>
          <Form.Item name="webhook_method" label={copy.fields.method}>
            <Select options={['POST', 'PUT', 'PATCH'].map((method) => ({ value: method, label: method }))} />
          </Form.Item>
          <Form.Item name="webhook_headers_json" label={copy.fields.webhookHeaders}>
            <TextArea rows={4} placeholder='{"Authorization":"Bearer xxx"}' />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
