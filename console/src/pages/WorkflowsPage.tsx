import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, Select, InputNumber, Switch, Space, message, Tag, List, Popconfirm, Divider, Card } from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, MinusCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { workflowApi, toolApi, type ResourceUsage } from '../api';
import { friendlyError } from '../utils/friendlyError';

const { TextArea } = Input;

const STEP_TYPES = [
  { value: 'collect', label: '收集信息' },
  { value: 'validate', label: '校验' },
  { value: 'tool_call', label: '工具调用' },
  { value: 'confirm', label: '用户确认' },
  { value: 'human_review', label: '人工审核' },
  { value: 'complete', label: '完成' },
];

const FAILURE_ACTIONS = [
  { value: 'retry', label: '重试' },
  { value: 'skip', label: '跳过' },
  { value: 'rollback', label: '回退' },
  { value: 'escalate', label: '转人工' },
];

const FIELD_TYPES = [
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'phone', label: 'Phone' },
  { value: 'date', label: 'Date' },
  { value: 'email', label: 'Email' },
  { value: 'select', label: 'Select' },
  { value: 'file', label: 'File' },
];

export default function WorkflowsPage() {
  const { t } = useTranslation();
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [tools, setTools] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [stepModalOpen, setStepModalOpen] = useState(false);
  const [selectedWf, setSelectedWf] = useState<any>(null);
  const [editingStep, setEditingStep] = useState<any>(null);
  const [form] = Form.useForm();
  const [stepForm] = Form.useForm();
  const stepType = Form.useWatch('step_type', stepForm);

  const load = async () => {
    setLoading(true);
    try {
      const [wfRes, toolRes] = await Promise.all([
        workflowApi.list(),
        toolApi.list(),
      ]);
      setWorkflows(wfRes.data);
      setTools(toolRes.data);
    } catch (e: any) {
      message.error('加载数据失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const toolOptions = tools.map((tool) => ({ value: tool.id, label: `${tool.name} (${tool.category})` }));

  const handleCreateWf = async () => {
    try {
      const values = await form.validateFields();
      await workflowApi.create(values);
      message.success('工作流已创建');
      setModalOpen(false);
      form.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error('创建失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const handleDeleteWf = async (id: string) => {
    try {
      await workflowApi.delete(id);
      message.success('已删除');
      load();
    } catch (e: any) {
      message.error('删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const confirmPlainDeleteWf = (id: string) => {
    Modal.confirm({
      title: '确认删除此工作流及其所有步骤？',
      okText: '确认',
      cancelText: '取消',
      onOk: () => handleDeleteWf(id),
    });
  };

  const handleDeleteWfClick = async (id: string) => {
    let usage: ResourceUsage | null = null;
    try {
      usage = (await workflowApi.usage(id)).data;
    } catch (e: unknown) {
      message.error(`${t('workflows.usageCheckFailed')}: ${friendlyError(e, t)}`);
      confirmPlainDeleteWf(id);
      return;
    }

    if (usage.count > 0) {
      const names = usage.used_by.slice(0, 5).map((a) => a.agent_name);
      const more = usage.count > 5 ? t('workflows.deleteInUseMore', { count: usage.count - 5 }) : '';
      Modal.confirm({
        title: t('workflows.deleteInUseTitle', { count: usage.count }),
        content: (
          <div>
            <p>{names.join(', ')}{more}</p>
            <p>{t('workflows.deleteInUseBody')}</p>
          </div>
        ),
        okText: t('workflows.deleteAnyway'),
        okType: 'danger',
        cancelText: '取消',
        onOk: () => handleDeleteWf(id),
      });
      return;
    }

    confirmPlainDeleteWf(id);
  };

  const openEditStep = (wf: any, step: any) => {
    setSelectedWf(wf);
    setEditingStep(step);
    const formValues: any = {
      ...step,
      fields: (step.fields || []).map((f: any) => ({
        ...f,
        options: f.options ? JSON.stringify(f.options, null, 2) : '',
      })),
    };
    // Webhook headers: object -> JSON string for TextArea editing
    if (step.tool_config?.webhook_headers && typeof step.tool_config.webhook_headers === 'object') {
      formValues.tool_config = {
        ...step.tool_config,
        webhook_headers: JSON.stringify(step.tool_config.webhook_headers, null, 2),
      };
    }
    stepForm.setFieldsValue(formValues);
    setStepModalOpen(true);
  };

  const handleSaveStep = async () => {
    if (!selectedWf) return;
    try {
      const values = await stepForm.validateFields();
      const st = values.step_type;

      // collect: process fields — options JSON string -> array
      if (st === 'collect' && values.fields?.length) {
        values.fields = values.fields.map((f: any) => {
          const field = { ...f };
          if (f.field_type === 'select' && f.options) {
            try { field.options = JSON.parse(f.options); } catch { delete field.options; }
          } else {
            delete field.options;
          }
          return field;
        });
      } else {
        values.fields = null;
      }

      // Clean type-specific fields
      if (st !== 'tool_call') values.tool_id = null;
      if (st !== 'confirm') values.requires_human_confirm = false;

      // complete: webhook — default method + headers JSON string -> object
      if (st === 'complete' && values.tool_config?.webhook_enabled) {
        values.tool_config.webhook_method = values.tool_config.webhook_method || 'POST';
        if (values.tool_config.webhook_headers && typeof values.tool_config.webhook_headers === 'string') {
          try {
            values.tool_config.webhook_headers = JSON.parse(values.tool_config.webhook_headers);
          } catch {
            delete values.tool_config.webhook_headers;
          }
        }
      } else if (st !== 'tool_call') {
        values.tool_config = null;
      }

      if (editingStep) {
        await workflowApi.updateStep(selectedWf.id, editingStep.id, values);
        message.success('步骤已更新');
      } else {
        await workflowApi.addStep(selectedWf.id, values);
        message.success('步骤已添加');
      }
      setStepModalOpen(false);
      setEditingStep(null);
      stepForm.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error('保存失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const handleDeleteStep = async (wfId: string, stepId: string) => {
    try {
      await workflowApi.deleteStep(wfId, stepId);
      message.success('步骤已删除');
      load();
    } catch (e: any) {
      message.error('删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    { title: '步骤数', key: 'steps', render: (_: any, r: any) => r.steps?.length || 0 },
    { title: '版本', dataIndex: 'version', key: 'version' },
    {
      title: '操作', key: 'actions', render: (_: any, record: any) => (
        <Space>
          <Button icon={<PlusOutlined />} size="small" onClick={() => { setSelectedWf(record); setEditingStep(null); stepForm.resetFields(); setStepModalOpen(true); }}>
            添加步骤
          </Button>
          <Button icon={<DeleteOutlined />} size="small" danger onClick={() => handleDeleteWfClick(record.id)}>删除</Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>流程编排</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setModalOpen(true); }}>
          创建工作流
        </Button>
      </div>

      <Table
        dataSource={workflows}
        columns={columns}
        rowKey="id"
        loading={loading}
        expandable={{
          expandedRowRender: (record) => (
            <List
              size="small"
              header={<strong>步骤列表</strong>}
              dataSource={record.steps || []}
              renderItem={(step: any) => (
                <List.Item
                  actions={[
                    <Button size="small" icon={<EditOutlined />} onClick={() => openEditStep(record, step)}>编辑</Button>,
                    <Popconfirm title="确认删除此步骤？" onConfirm={() => handleDeleteStep(record.id, step.id)} okText="确认" cancelText="取消">
                      <Button size="small" danger>删除</Button>
                    </Popconfirm>,
                  ]}
                >
                  <List.Item.Meta
                    avatar={<Tag color="blue">{step.order}</Tag>}
                    title={`${step.name} (${step.step_type})`}
                    description={
                      <div>
                        <div>{step.prompt_template || '无提示语'}</div>
                        {step.step_type === 'collect' && step.fields?.length > 0 && (
                          <div style={{ marginTop: 4 }}>
                            {step.fields.map((f: any) => (
                              <Tag key={f.name} color="cyan">{f.label || f.name}</Tag>
                            ))}
                          </div>
                        )}
                        {step.step_type === 'complete' && step.tool_config?.webhook_enabled && (
                          <Tag color="green" style={{ marginTop: 4 }}>Webhook: {step.tool_config.webhook_url}</Tag>
                        )}
                      </div>
                    }
                  />
                  <Space>
                    {step.requires_human_confirm && <Tag color="orange">需确认</Tag>}
                    {step.tool_id && <Tag color="purple">绑定工具</Tag>}
                    <Tag>{step.on_failure}</Tag>
                  </Space>
                </List.Item>
              )}
            />
          ),
        }}
      />

      {/* Create workflow modal */}
      <Modal title="创建工作流" open={modalOpen} onOk={handleCreateWf} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如：居住证办理" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={2} placeholder="描述流程用途" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Add/Edit step modal */}
      <Modal
        title={editingStep ? `编辑步骤 - ${selectedWf?.name || ''}` : `添加步骤 - ${selectedWf?.name || ''}`}
        open={stepModalOpen}
        onOk={handleSaveStep}
        onCancel={() => { setStepModalOpen(false); setEditingStep(null); }}
        width={720}
        destroyOnClose
      >
        <Form
          form={stepForm}
          layout="vertical"
          initialValues={{
            step_type: 'collect',
            on_failure: 'retry',
            max_retries: 2,
            risk_level: 'info',
            requires_human_confirm: false,
          }}
        >
          {/* ── Common fields ── */}
          <Form.Item name="name" label="步骤名称" rules={[{ required: true }]}>
            <Input placeholder="如：填写个人信息" />
          </Form.Item>
          <Form.Item name="order" label="顺序号" rules={[{ required: true }]}>
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="step_type" label="步骤类型">
            <Select options={STEP_TYPES} />
          </Form.Item>
          <Form.Item name="prompt_template" label="提示语模板">
            <TextArea rows={3} placeholder="请填写您的姓名、身份证号..." />
          </Form.Item>
          <Form.Item name="on_failure" label="失败策略">
            <Select options={FAILURE_ACTIONS} />
          </Form.Item>
          <Form.Item name="max_retries" label="最大重试次数">
            <InputNumber min={0} max={10} />
          </Form.Item>
          <Form.Item name="risk_level" label="风险等级">
            <Select options={[
              { value: 'info', label: '普通' },
              { value: 'warning', label: '警告' },
              { value: 'critical', label: '严重' },
            ]} />
          </Form.Item>

          {/* ── collect: form fields editor ── */}
          {stepType === 'collect' && (
            <>
              <Divider orientation="left">表单字段配置</Divider>
              <Form.List name="fields">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map(({ key, name, ...restField }) => (
                      <Card
                        key={key}
                        size="small"
                        style={{ marginBottom: 8 }}
                        title={`字段 #${name + 1}`}
                        extra={<MinusCircleOutlined style={{ color: '#ff4d4f' }} onClick={() => remove(name)} />}
                      >
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                          <Form.Item
                            {...restField}
                            name={[name, 'name']}
                            label="字段名"
                            rules={[{ required: true, message: '必填' }]}
                            style={{ flex: 1, minWidth: 120 }}
                          >
                            <Input placeholder="field_name" />
                          </Form.Item>
                          <Form.Item
                            {...restField}
                            name={[name, 'label']}
                            label="显示标签"
                            rules={[{ required: true, message: '必填' }]}
                            style={{ flex: 1, minWidth: 120 }}
                          >
                            <Input placeholder="姓名" />
                          </Form.Item>
                          <Form.Item
                            {...restField}
                            name={[name, 'field_type']}
                            label="类型"
                            style={{ width: 110 }}
                          >
                            <Select options={FIELD_TYPES} />
                          </Form.Item>
                          <Form.Item
                            {...restField}
                            name={[name, 'required']}
                            label="必填"
                            valuePropName="checked"
                          >
                            <Switch />
                          </Form.Item>
                        </div>
                        <Form.Item {...restField} name={[name, 'placeholder']} label="占位符">
                          <Input placeholder="请输入..." />
                        </Form.Item>
                        {/* options: only for select type */}
                        <Form.Item shouldUpdate noStyle>
                          {({ getFieldValue }) => {
                            const ft = getFieldValue(['fields', name, 'field_type']);
                            if (ft !== 'select') return null;
                            return (
                              <Form.Item
                                name={[name, 'options']}
                                label="选项 (JSON)"
                                extra='格式: [{"label":"选项1","value":"v1"}, {"label":"选项2","value":"v2"}]'
                              >
                                <TextArea rows={2} placeholder='[{"label":"选项1","value":"v1"}]' />
                              </Form.Item>
                            );
                          }}
                        </Form.Item>
                      </Card>
                    ))}
                    <Button type="dashed" onClick={() => add({ field_type: 'text', required: true })} block icon={<PlusOutlined />}>
                      添加字段
                    </Button>
                  </>
                )}
              </Form.List>
            </>
          )}

          {/* ── tool_call: tool binding ── */}
          {stepType === 'tool_call' && (
            <>
              <Divider orientation="left">工具绑定</Divider>
              <Form.Item name="tool_id" label="选择工具" rules={[{ required: true, message: '工具调用步骤必须选择工具' }]}>
                <Select allowClear placeholder="选择工具" options={toolOptions} />
              </Form.Item>
              <Form.Item shouldUpdate noStyle>
                {({ getFieldValue }) => {
                  const tid = getFieldValue('tool_id');
                  const tool = tools.find(t => t.id === tid);
                  if (!tool) return null;
                  return (
                    <div style={{ color: '#888', marginBottom: 16, fontSize: 12 }}>
                      Endpoint: {tool.endpoint_url} ({tool.method})
                    </div>
                  );
                }}
              </Form.Item>
            </>
          )}

          {/* ── confirm: human confirm switch ── */}
          {stepType === 'confirm' && (
            <>
              <Divider orientation="left">确认配置</Divider>
              <Form.Item name="requires_human_confirm" label="需要人工确认" valuePropName="checked">
                <Switch />
              </Form.Item>
            </>
          )}

          {/* ── complete: webhook config ── */}
          {stepType === 'complete' && (
            <>
              <Divider orientation="left">Webhook 配置</Divider>
              <Form.Item name={['tool_config', 'webhook_enabled']} label="完成时发送到外部接口" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item shouldUpdate noStyle>
                {({ getFieldValue }) => {
                  if (!getFieldValue(['tool_config', 'webhook_enabled'])) return null;
                  return (
                    <>
                      <Form.Item
                        name={['tool_config', 'webhook_url']}
                        label="接口地址"
                        rules={[{ required: true, message: '开启 Webhook 时必须填写接口地址' }]}
                      >
                        <Input placeholder="https://example.com/webhook" />
                      </Form.Item>
                      <Form.Item name={['tool_config', 'webhook_method']} label="请求方法">
                        <Select options={[
                          { value: 'POST', label: 'POST' },
                          { value: 'PUT', label: 'PUT' },
                          { value: 'PATCH', label: 'PATCH' },
                        ]} />
                      </Form.Item>
                      <Form.Item
                        name={['tool_config', 'webhook_headers']}
                        label="请求头 (JSON, 可选)"
                        extra='格式: {"Authorization":"Bearer xxx"}'
                      >
                        <TextArea rows={2} placeholder='{"Authorization":"Bearer xxx"}' />
                      </Form.Item>
                    </>
                  );
                }}
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}
