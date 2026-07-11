import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, Select, InputNumber, Switch, Space, message, Tag } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { toolApi, type ResourceUsage } from '../api';
import { friendlyError } from '../utils/friendlyError';

const { TextArea } = Input;

const BUILTIN_FUNCTIONS = [
  { value: 'calculator', label: '计算器 (Calculator)' },
  { value: 'weather', label: '天气查询 (Weather)' },
  { value: 'unit_converter', label: '单位转换 (Unit Converter)' },
  { value: 'timestamp', label: '时间戳 (Timestamp)' },
];

export default function ToolsPage() {
  const { t } = useTranslation();
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
      message.error('加载工具列表失败');
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
          message.error('输入 Schema JSON 格式无效');
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
        message.success('工具已更新');
      } else {
        await toolApi.create(values);
        message.success('工具已注册');
      }
      setModalOpen(false);
      setEditing(null);
      form.resetFields();
      setCategory('api');
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error('保存失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await toolApi.delete(id);
      message.success('已删除');
      load();
    } catch (e: any) {
      message.error('删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const confirmPlainDelete = (id: string) => {
    Modal.confirm({
      title: '确认删除此工具？',
      okText: '确认',
      cancelText: '取消',
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
        cancelText: '取消',
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
        message.success(`连通性测试通过 (${res.data.latency_ms?.toFixed(0)}ms)`);
      } else {
        message.error(`测试失败: ${res.data.error}`);
      }
    } catch {
      message.error('测试请求失败');
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
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '类型', dataIndex: 'category', key: 'category', render: (v: string) => <Tag>{v}</Tag> },
    { title: '方法', dataIndex: 'method', key: 'method' },
    { title: '端点', dataIndex: 'endpoint', key: 'endpoint', ellipsis: true, render: (v: string, r: any) => r.category === 'function' ? <Tag color="blue">内置函数</Tag> : v },
    { title: '超时(ms)', dataIndex: 'timeout_ms', key: 'timeout_ms' },
    {
      title: '风险', dataIndex: 'risk_level', key: 'risk_level',
      render: (v: string) => <Tag color={v === 'critical' ? 'red' : v === 'warning' ? 'orange' : 'green'}>{v}</Tag>,
    },
    { title: '状态', dataIndex: 'enabled', key: 'enabled', render: (v: boolean) => <Tag color={v ? 'green' : 'red'}>{v ? '启用' : '停用'}</Tag> },
    {
      title: '操作', key: 'actions', render: (_: any, record: any) => (
        <Space>
          <Button icon={<ThunderboltOutlined />} size="small" onClick={() => handleTest(record.id)}>测试</Button>
          <Button icon={<EditOutlined />} size="small" onClick={() => openEdit(record)}>编辑</Button>
          <Button icon={<DeleteOutlined />} size="small" danger onClick={() => handleDeleteClick(record.id)}>删除</Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>工具管理</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); form.resetFields(); setCategory('api'); setModalOpen(true); }}>
          注册工具
        </Button>
      </div>

      <Table dataSource={tools} columns={columns} rowKey="id" loading={loading} />

      <Modal title={editing ? '编辑工具' : '注册工具'} open={modalOpen} onOk={handleSave} onCancel={() => setModalOpen(false)} width={640} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input placeholder="如: 身份校验接口" /></Form.Item>
          <Form.Item name="description" label="描述"><Input /></Form.Item>
          <Form.Item name="category" label="类型" initialValue="api">
            <Select onChange={(v) => setCategory(v)} options={[
              { value: 'api', label: 'HTTP API' },
              { value: 'function', label: '内置函数' },
              { value: 'webhook', label: 'Webhook' },
              { value: 'rpc', label: 'RPC' },
            ]} />
          </Form.Item>

          {category === 'function' ? (
            <Form.Item name="name" label="内置函数" tooltip="选择内置函数后，名称将自动设置">
              <Select placeholder="选择内置函数" options={BUILTIN_FUNCTIONS} />
            </Form.Item>
          ) : (
            <Form.Item name="endpoint" label="端点 URL" rules={[{ required: category !== 'function' }]}>
              <Input placeholder="https://api.example.com/verify" />
            </Form.Item>
          )}

          <Form.Item name="method" label="HTTP 方法" initialValue="POST">
            <Select options={['GET', 'POST', 'PUT', 'DELETE'].map(m => ({ value: m, label: m }))} />
          </Form.Item>
          <Form.Item name="input_schema_json" label="输入 Schema (JSON)">
            <TextArea rows={4} placeholder={'{\n  "type": "object",\n  "properties": {\n    "city": {"type": "string", "description": "城市名称"}\n  },\n  "required": ["city"]\n}'} />
          </Form.Item>
          <Form.Item name="timeout_ms" label="超时 (ms)" initialValue={30000}>
            <InputNumber min={1000} max={300000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="max_retries" label="最大重试" initialValue={2}>
            <InputNumber min={0} max={10} />
          </Form.Item>
          <Form.Item name="is_async" label="异步模式" valuePropName="checked" initialValue={false}>
            <Switch />
          </Form.Item>
          <Form.Item name="risk_level" label="风险等级" initialValue="info">
            <Select options={[
              { value: 'info', label: '普通' },
              { value: 'warning', label: '警告' },
              { value: 'critical', label: '严重' },
            ]} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked" initialValue={true}>
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
