import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, Select, InputNumber, Switch, Space, message, Tag } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { toolApi, type ResourceUsage } from '../api';
import { friendlyError } from '../utils/friendlyError';

const { TextArea } = Input;

export default function ToolsPage() {
  const { t } = useTranslation();

  const BUILTIN_FUNCTIONS = [
    { value: 'calculator', label: `${t('tools.builtinFunctions.calculator')} (Calculator)` },
    { value: 'weather', label: `${t('tools.builtinFunctions.weather')} (Weather)` },
    { value: 'unit_converter', label: `${t('tools.builtinFunctions.unitConverter')} (Unit Converter)` },
    { value: 'timestamp', label: `${t('tools.builtinFunctions.timestamp')} (Timestamp)` },
  ];
  const [tools, setTools] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [category, setCategory] = useState('api');
  const [form] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      const res = await toolApi.list();
      setTools(res.data);
    } catch (e: any) {
      message.error(t('tools.loadFailed'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      // Parse input_schema JSON
      if (values.input_schema_json) {
        try {
          values.input_schema = JSON.parse(values.input_schema_json);
        } catch {
          message.error(t('tools.invalidSchemaJson'));
          return;
        }
      }
      delete values.input_schema_json;

      // For function tools, use the function name as the tool name if creating
      if (values.category === 'function' && !values.endpoint) {
        values.endpoint = '';
      }

      if (editing) {
        await toolApi.update(editing.id, values);
        message.success(t('tools.updated'));
      } else {
        await toolApi.create(values);
        message.success(t('tools.registered'));
      }
      setModalOpen(false);
      setEditing(null);
      form.resetFields();
      setCategory('api');
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(`${t('tools.saveFailed')}: ` + (e.response?.data?.detail || e.message || t('common.unknown')));
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await toolApi.delete(id);
      message.success(t('tools.deleted'));
      load();
    } catch (e: any) {
      message.error(`${t('tools.deleteFailed')}: ` + (e.response?.data?.detail || e.message || t('common.unknown')));
    }
  };

  const confirmPlainDelete = (id: string) => {
    Modal.confirm({
      title: t('tools.deleteConfirm'),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: () => handleDelete(id),
    });
  };

  const handleDeleteClick = async (id: string) => {
    let usage: ResourceUsage | null = null;
    try {
      usage = (await toolApi.usage(id)).data;
    } catch (e: unknown) {
      message.error(`${t('tools.usageCheckFailed')}: ${friendlyError(e, t)}`);
      confirmPlainDelete(id);
      return;
    }

    if (usage.count > 0) {
      const names = usage.used_by.slice(0, 5).map((a) => a.agent_name);
      const more = usage.count > 5 ? t('tools.deleteInUseMore', { count: usage.count - 5 }) : '';
      Modal.confirm({
        title: t('tools.deleteInUseTitle', { count: usage.count }),
        content: (
          <div>
            <p>{names.join(', ')}{more}</p>
            <p>{t('tools.deleteInUseBody')}</p>
          </div>
        ),
        okText: t('tools.deleteAnyway'),
        okType: 'danger',
        cancelText: t('common.cancel'),
        onOk: () => handleDelete(id),
      });
      return;
    }

    confirmPlainDelete(id);
  };

  const handleTest = async (id: string) => {
    try {
      const res = await toolApi.test({ tool_id: id });
      if (res.data.success) {
        message.success(t('tools.testPassed', { ms: res.data.latency_ms?.toFixed(0) }));
      } else {
        message.error(`${t('tools.testFailedPrefix')}: ${res.data.error}`);
      }
    } catch {
      message.error(t('tools.testRequestFailed'));
    }
  };

  const openEdit = (record: any) => {
    setEditing(record);
    setCategory(record.category || 'api');
    form.setFieldsValue({
      ...record,
      input_schema_json: record.input_schema ? JSON.stringify(record.input_schema, null, 2) : '',
    });
    setModalOpen(true);
  };

  const columns = [
    { title: t('common.name'), dataIndex: 'name', key: 'name' },
    { title: t('tools.category'), dataIndex: 'category', key: 'category', render: (v: string) => <Tag>{v}</Tag> },
    { title: t('tools.method'), dataIndex: 'method', key: 'method' },
    { title: t('tools.endpoint'), dataIndex: 'endpoint', key: 'endpoint', ellipsis: true, render: (v: string, r: any) => r.category === 'function' ? <Tag color="blue">{t('tools.categories.function')}</Tag> : v },
    { title: t('tools.timeout'), dataIndex: 'timeout_ms', key: 'timeout_ms' },
    {
      title: t('tools.riskLevel'), dataIndex: 'risk_level', key: 'risk_level',
      render: (v: string) => <Tag color={v === 'critical' ? 'red' : v === 'warning' ? 'orange' : 'green'}>{v}</Tag>,
    },
    { title: t('common.status'), dataIndex: 'enabled', key: 'enabled', render: (v: boolean) => <Tag color={v ? 'green' : 'red'}>{v ? t('common.enabled') : t('common.disabled')}</Tag> },
    {
      title: t('common.actions'), key: 'actions', render: (_: any, record: any) => (
        <Space>
          <Button icon={<ThunderboltOutlined />} size="small" onClick={() => handleTest(record.id)}>{t('common.test')}</Button>
          <Button icon={<EditOutlined />} size="small" onClick={() => openEdit(record)}>{t('common.edit')}</Button>
          <Button icon={<DeleteOutlined />} size="small" danger onClick={() => handleDeleteClick(record.id)}>{t('common.delete')}</Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>{t('tools.title')}</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); form.resetFields(); setCategory('api'); setModalOpen(true); }}>
          {t('tools.registerButton')}
        </Button>
      </div>

      <Table dataSource={tools} columns={columns} rowKey="id" loading={loading} />

      <Modal title={editing ? t('tools.edit') : t('tools.registerModalTitle')} open={modalOpen} onOk={handleSave} onCancel={() => setModalOpen(false)} width={640} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label={t('common.name')} rules={[{ required: true }]}><Input placeholder={t('tools.namePlaceholder')} /></Form.Item>
          <Form.Item name="description" label={t('common.description')}><Input /></Form.Item>
          <Form.Item name="category" label={t('common.type')} initialValue="api">
            <Select onChange={(v) => setCategory(v)} options={[
              { value: 'api', label: 'HTTP API' },
              { value: 'function', label: t('tools.categories.function') },
              { value: 'webhook', label: 'Webhook' },
              { value: 'rpc', label: 'RPC' },
            ]} />
          </Form.Item>

          {category === 'function' ? (
            <Form.Item name="name" label={t('tools.builtinFunctionLabel')} tooltip={t('tools.builtinFunctionTooltip')}>
              <Select placeholder={t('tools.selectBuiltinPlaceholder')} options={BUILTIN_FUNCTIONS} />
            </Form.Item>
          ) : (
            <Form.Item name="endpoint" label={t('tools.endpoint')} rules={[{ required: category !== 'function' }]}>
              <Input placeholder="https://api.example.com/verify" />
            </Form.Item>
          )}

          <Form.Item name="method" label={t('tools.method')} initialValue="POST">
            <Select options={['GET', 'POST', 'PUT', 'DELETE'].map(m => ({ value: m, label: m }))} />
          </Form.Item>
          <Form.Item name="input_schema_json" label={t('tools.inputSchema')}>
            <TextArea rows={4} placeholder={`{\n  "type": "object",\n  "properties": {\n    "city": {"type": "string", "description": "${t('tools.inputSchemaExampleDescription')}"}\n  },\n  "required": ["city"]\n}`} />
          </Form.Item>
          <Form.Item name="timeout_ms" label={t('tools.timeout')} initialValue={30000}>
            <InputNumber min={1000} max={300000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="max_retries" label={t('tools.maxRetries')} initialValue={2}>
            <InputNumber min={0} max={10} />
          </Form.Item>
          <Form.Item name="is_async" label={t('tools.asyncModeLabel')} valuePropName="checked" initialValue={false}>
            <Switch />
          </Form.Item>
          <Form.Item name="risk_level" label={t('tools.riskLevel')} initialValue="info">
            <Select options={[
              { value: 'info', label: t('workflows.riskLevels.info') },
              { value: 'warning', label: t('workflows.riskLevels.warning') },
              { value: 'critical', label: t('workflows.riskLevels.critical') },
            ]} />
          </Form.Item>
          <Form.Item name="enabled" label={t('common.enabled')} valuePropName="checked" initialValue={true}>
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
