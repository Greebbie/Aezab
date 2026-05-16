import { useState } from 'react';
import { ApiOutlined, PlusOutlined, RobotOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Modal, Select, Space, Table, Tag, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { agentCapabilitiesApi, toolApi } from '../../api';
import type { Agent, AgentCapabilities, Tool } from '../../types';
import type { IntegrationCopy } from './copy';
import IntegrationCodeBlock from './IntegrationCodeBlock';
import { buildToolRegistrationSnippet } from './snippets';

const { TextArea } = Input;
const CONNECT_EXTERNAL_API_LABEL = 'Connect External API';

interface Props {
  agents: Agent[];
  apiBase: string;
  tools: Tool[];
  copy: IntegrationCopy;
  onChanged: () => void | Promise<void>;
}

interface ToolFormValues {
  name: string;
  description?: string;
  endpoint: string;
  method?: string;
  input_schema_json?: string;
  auth_type?: 'none' | 'bearer' | 'api_key';
  auth_header?: string;
  auth_token?: string;
}

interface BindFormValues {
  agent_id: string;
  description?: string;
  keywords?: string;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseJsonObjectField(value: string | undefined, fallback: Record<string, unknown>) {
  if (!value?.trim()) return fallback;
  const parsed = JSON.parse(value) as unknown;
  if (!isPlainObject(parsed)) {
    throw new SyntaxError('Expected JSON object');
  }
  return parsed;
}

function parseInputSchema(value: string | undefined) {
  const schema = parseJsonObjectField(value, {
    type: 'object',
    properties: {},
    required: [],
  });

  if ('type' in schema && typeof schema.type !== 'string') {
    throw new SyntaxError('Schema type must be a string');
  }
  if ('properties' in schema && !isPlainObject(schema.properties)) {
    throw new SyntaxError('Schema properties must be an object');
  }
  if ('required' in schema && !Array.isArray(schema.required)) {
    throw new SyntaxError('Schema required must be an array');
  }

  return schema;
}

function sampleValueForProperty(propertySchema: unknown) {
  if (!isPlainObject(propertySchema)) return '';
  if ('default' in propertySchema) return propertySchema.default;
  if (Array.isArray(propertySchema.enum) && propertySchema.enum.length > 0) return propertySchema.enum[0];

  switch (propertySchema.type) {
    case 'number':
    case 'integer':
      return 0;
    case 'boolean':
      return false;
    case 'array':
      return [];
    case 'object':
      return {};
    case 'string':
    default:
      return '';
  }
}

function buildDefaultTestInput(schema: Tool['input_schema']) {
  if (!isPlainObject(schema) || !Array.isArray(schema.required) || !isPlainObject(schema.properties)) {
    return {};
  }

  const properties = schema.properties;
  return schema.required.reduce<Record<string, unknown>>((input, field) => {
    if (typeof field === 'string') {
      input[field] = sampleValueForProperty(properties[field]);
    }
    return input;
  }, {});
}

function parseKeywords(value: string | undefined) {
  if (!value?.trim()) return [];
  return value
    .split(/[,\n，、]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeCapabilities(caps: AgentCapabilities): AgentCapabilities {
  return {
    knowledge: caps.knowledge || [],
    workflows: caps.workflows || [],
    tools: caps.tools || [],
  };
}

export default function OutboundToolsTab({ agents, apiBase, tools, copy, onChanged }: Props) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [bindingTool, setBindingTool] = useState<Tool | null>(null);
  const [bindingSaving, setBindingSaving] = useState(false);
  const [testToolModal, setTestToolModal] = useState<Tool | null>(null);
  const [testInputJson, setTestInputJson] = useState('{}');
  const [form] = Form.useForm<ToolFormValues>();
  const [bindForm] = Form.useForm<BindFormValues>();

  const openWizard = () => {
    form.resetFields();
    setOpen(true);
  };

  const createTool = async (values: ToolFormValues) => {
    const inputSchema = parseInputSchema(values.input_schema_json);
    const authConfig =
      values.auth_type === 'api_key'
        ? {
            type: 'api_key',
            header: values.auth_header || 'X-API-Key',
            token: values.auth_token,
          }
        : values.auth_type === 'bearer'
          ? {
              type: 'bearer',
              token: values.auth_token,
            }
          : null;

    const res = await toolApi.create({
      name: values.name,
      description: values.description || '',
      category: 'api',
      endpoint: values.endpoint,
      method: values.method || 'POST',
      input_schema: inputSchema,
      auth_config: authConfig || undefined,
      timeout_ms: 30000,
      max_retries: 2,
      risk_level: 'info',
    });
    return res.data;
  };

  const openBindModal = (tool: Tool) => {
    bindForm.resetFields();
    bindForm.setFieldsValue({
      description: tool.description || `Use ${tool.name} when the user asks for this backend action.`,
      keywords: '',
    });
    setBindingTool(tool);
  };

  const bindToolToAgent = async () => {
    if (!bindingTool) return;

    try {
      const values = await bindForm.validateFields();
      setBindingSaving(true);
      const capsRes = await agentCapabilitiesApi.get(values.agent_id);
      const nextCaps = normalizeCapabilities(capsRes.data);
      const keywords = parseKeywords(values.keywords);
      const description = values.description || bindingTool.description || `Call ${bindingTool.name}`;
      const existing = nextCaps.tools.find((cap) => cap.tool_ids.includes(bindingTool.id));

      if (existing) {
        existing.tool_ids = Array.from(new Set([...existing.tool_ids, bindingTool.id]));
        existing.description = description;
        existing.keywords = keywords;
      } else {
        nextCaps.tools.push({
          tool_ids: [bindingTool.id],
          description,
          keywords,
        });
      }

      await agentCapabilitiesApi.update(values.agent_id, nextCaps);
      message.success(copy.messages.toolBoundToAgent);
      setBindingTool(null);
      bindForm.resetFields();
      await onChanged();
    } catch (error) {
      if (typeof error === 'object' && error && 'errorFields' in error) return;
      message.error(error instanceof Error ? error.message : copy.messages.bindToolFailed);
    } finally {
      setBindingSaving(false);
    }
  };

  const save = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const createdTool = await createTool(values);
      message.success(copy.messages.toolCreated);
      setOpen(false);
      form.resetFields();
      await onChanged();
      if (createdTool && agents.length > 0) {
        openBindModal(createdTool);
      }
    } catch (error) {
      if (typeof error === 'object' && error && 'errorFields' in error) return;
      if (error instanceof SyntaxError) {
        message.error(copy.messages.invalidSchema);
      } else {
        message.error(error instanceof Error ? error.message : copy.messages.toolTestFailed);
      }
    } finally {
      setSaving(false);
    }
  };

  const openTestModal = (tool: Tool) => {
    setTestToolModal(tool);
    setTestInputJson(JSON.stringify(buildDefaultTestInput(tool.input_schema), null, 2));
  };

  const testTool = async () => {
    if (!testToolModal) return;

    let testInput: Record<string, unknown>;
    try {
      testInput = parseJsonObjectField(testInputJson, {});
    } catch {
      message.error(copy.messages.invalidTestInput);
      return;
    }

    setTestingId(testToolModal.id);
    try {
      const res = await toolApi.test({ tool_id: testToolModal.id, test_input: testInput });
      if (res.data.success) {
        message.success(copy.messages.toolTestPassed);
        setTestToolModal(null);
      } else {
        message.error(`${copy.messages.toolTestFailed}: ${res.data.error || '-'}`);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : copy.messages.toolTestFailed);
    } finally {
      setTestingId(null);
    }
  };

  const columns: ColumnsType<Tool> = [
    {
      title: copy.fields.toolName,
      dataIndex: 'name',
      key: 'name',
      ellipsis: true,
      render: (name: string, record) => (
        <Space direction="vertical" size={0}>
          <span>{name}</span>
          {record.description ? <span style={{ color: '#8c8c8c', fontSize: 12 }}>{record.description}</span> : null}
        </Space>
      ),
    },
    {
      title: 'Type',
      dataIndex: 'category',
      key: 'category',
      width: 100,
      render: (category: string) => <Tag>{category}</Tag>,
    },
    { title: copy.fields.method, dataIndex: 'method', key: 'method', width: 90 },
    { title: copy.fields.endpoint, dataIndex: 'endpoint', key: 'endpoint', ellipsis: true },
    {
      title: copy.fields.status,
      dataIndex: 'enabled',
      key: 'enabled',
      width: 110,
      render: (enabled: boolean) => <Tag color={enabled ? 'green' : 'red'}>{enabled ? 'Enabled' : 'Disabled'}</Tag>,
    },
    {
      title: '',
      key: 'actions',
      width: 250,
      render: (_, record) => (
        <Space wrap>
          <Button
            size="small"
            icon={<ThunderboltOutlined />}
            loading={testingId === record.id}
            onClick={() => openTestModal(record)}
          >
            {copy.actions.testTool}
          </Button>
          <Button size="small" icon={<RobotOutlined />} onClick={() => openBindModal(record)}>
            {copy.actions.bindToolToAgent}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Alert type="info" showIcon message={copy.bindingNote} style={{ marginBottom: 16 }} />

      <Card
        title="Customer Backend APIs"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openWizard}>
            {copy.actions.connectExternalApi || CONNECT_EXTERNAL_API_LABEL}
          </Button>
        }
        style={{ marginBottom: 16 }}
      >
        <Table
          size="small"
          dataSource={tools}
          columns={columns}
          rowKey="id"
          pagination={{ pageSize: 6 }}
          locale={{ emptyText: copy.empty.noTools }}
        />
      </Card>

      <Card title="Register Tool API Example">
        <IntegrationCodeBlock value={buildToolRegistrationSnippet(apiBase)} copy={copy} />
      </Card>

      <Modal
        title={copy.actions.connectExternalApi || CONNECT_EXTERNAL_API_LABEL}
        open={open}
        onOk={save}
        okText={copy.actions.saveTool}
        confirmLoading={saving}
        onCancel={() => setOpen(false)}
        width={720}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            method: 'POST',
            auth_type: 'none',
            auth_header: 'X-API-Key',
            input_schema_json: '{\n  "type": "object",\n  "properties": {},\n  "required": []\n}',
          }}
        >
          <Form.Item name="name" label={copy.fields.toolName} rules={[{ required: true }]}>
            <Input prefix={<ApiOutlined />} placeholder="create_work_order" />
          </Form.Item>
          <Form.Item name="description" label={copy.fields.description}>
            <Input placeholder="Create a work order in the customer backend" />
          </Form.Item>
          <Form.Item name="endpoint" label={copy.fields.endpoint} rules={[{ required: true, type: 'url' }]}>
            <Input placeholder="https://customer-crm.example.com/api/work-orders" />
          </Form.Item>
          <Form.Item name="method" label={copy.fields.method}>
            <Select options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((method) => ({ value: method, label: method }))} />
          </Form.Item>
          <Form.Item name="input_schema_json" label={copy.fields.inputSchema}>
            <TextArea rows={8} />
          </Form.Item>
          <Space style={{ width: '100%' }} align="start" wrap>
            <Form.Item name="auth_type" label={copy.fields.authType} style={{ minWidth: 180 }}>
              <Select
                options={[
                  { value: 'none', label: 'None' },
                  { value: 'bearer', label: 'Bearer Token' },
                  { value: 'api_key', label: 'API Key Header' },
                ]}
              />
            </Form.Item>
            <Form.Item
              noStyle
              shouldUpdate={(prev, next) => prev.auth_type !== next.auth_type}
            >
              {({ getFieldValue }) =>
                getFieldValue('auth_type') === 'api_key' ? (
                  <Form.Item name="auth_header" label="Header Name" style={{ minWidth: 180 }}>
                    <Input placeholder="X-API-Key" />
                  </Form.Item>
                ) : null
              }
            </Form.Item>
            <Form.Item
              noStyle
              shouldUpdate={(prev, next) => prev.auth_type !== next.auth_type}
            >
              {({ getFieldValue }) => {
                const authType = getFieldValue('auth_type');
                return (
                  <Form.Item
                    name="auth_token"
                    label={copy.fields.authToken}
                    style={{ flex: 1, minWidth: 320 }}
                    rules={[{ required: authType === 'bearer' || authType === 'api_key' }]}
                  >
                    <Input.Password placeholder="<customer-system-token>" />
                  </Form.Item>
                );
              }}
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      <Modal
        title={copy.actions.testTool}
        open={!!testToolModal}
        onOk={testTool}
        okText={copy.actions.sendTest}
        confirmLoading={!!testToolModal && testingId === testToolModal.id}
        onCancel={() => setTestToolModal(null)}
        width={720}
        destroyOnClose
      >
        {testToolModal ? (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <div>
              <Tag>{testToolModal.method}</Tag>
              <span>{testToolModal.endpoint}</span>
            </div>
            <div>
              <div style={{ marginBottom: 8 }}>{copy.fields.testInput}</div>
              <TextArea
                rows={10}
                value={testInputJson}
                onChange={(event) => setTestInputJson(event.target.value)}
              />
            </div>
          </Space>
        ) : null}
      </Modal>

      <Modal
        title={copy.actions.bindToolToAgent}
        open={!!bindingTool}
        onOk={bindToolToAgent}
        okText={copy.actions.saveBinding}
        confirmLoading={bindingSaving}
        onCancel={() => setBindingTool(null)}
        width={680}
        destroyOnClose
      >
        <Form form={bindForm} layout="vertical">
          <Form.Item name="agent_id" label={copy.fields.agent} rules={[{ required: true }]}>
            <Select
              placeholder={copy.fields.selectAgent}
              options={agents.map((agent) => ({ value: agent.id, label: `${agent.name} (${agent.id})` }))}
            />
          </Form.Item>
          <Form.Item name="description" label={copy.fields.triggerDescription} rules={[{ required: true }]}>
            <TextArea
              rows={3}
              placeholder="Use this tool when the user wants to create a repair ticket in the customer backend."
            />
          </Form.Item>
          <Form.Item name="keywords" label={copy.fields.keywords}>
            <Input placeholder="repair, ticket, CRM, order lookup" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
