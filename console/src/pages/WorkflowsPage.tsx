import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, Select, InputNumber, Switch, Space, message, Tag, List, Popconfirm, Divider, Card } from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, MinusCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { workflowApi, toolApi, type ResourceUsage } from '../api';
import { friendlyError } from '../utils/friendlyError';

const { TextArea } = Input;

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

  const STEP_TYPES = [
    { value: 'collect', label: t('workflows.stepTypes.collect') },
    { value: 'validate', label: t('workflows.stepTypes.validate') },
    { value: 'tool_call', label: t('workflows.stepTypes.tool_call') },
    { value: 'confirm', label: t('workflows.stepTypes.confirm') },
    { value: 'human_review', label: t('workflows.stepTypes.human_review') },
    { value: 'complete', label: t('workflows.stepTypes.complete') },
  ];

  const FAILURE_ACTIONS = [
    { value: 'retry', label: t('workflows.failureStrategies.retry') },
    { value: 'skip', label: t('workflows.failureStrategies.skip') },
    { value: 'rollback', label: t('workflows.failureStrategies.rollback') },
    { value: 'escalate', label: t('workflows.failureStrategies.escalate') },
  ];
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
      message.error(t('workflows.loadFailed'));
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
      message.success(t('workflows.created'));
      setModalOpen(false);
      form.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(`${t('workflows.createFailed')}: ` + (e.response?.data?.detail || e.message || t('common.unknown')));
    }
  };

  const handleDeleteWf = async (id: string) => {
    try {
      await workflowApi.delete(id);
      message.success(t('workflows.deleted'));
      load();
    } catch (e: any) {
      message.error(`${t('workflows.deleteFailed')}: ` + (e.response?.data?.detail || e.message || t('common.unknown')));
    }
  };

  const confirmPlainDeleteWf = (id: string) => {
    Modal.confirm({
      title: t('workflows.deleteWorkflowConfirmTitle'),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
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
        cancelText: t('common.cancel'),
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
        message.success(t('workflows.stepUpdated'));
      } else {
        await workflowApi.addStep(selectedWf.id, values);
        message.success(t('workflows.stepAdded'));
      }
      setStepModalOpen(false);
      setEditingStep(null);
      stepForm.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(`${t('workflows.saveFailed')}: ` + (e.response?.data?.detail || e.message || t('common.unknown')));
    }
  };

  const handleDeleteStep = async (wfId: string, stepId: string) => {
    try {
      await workflowApi.deleteStep(wfId, stepId);
      message.success(t('workflows.stepDeleted'));
      load();
    } catch (e: any) {
      message.error(`${t('workflows.deleteFailed')}: ` + (e.response?.data?.detail || e.message || t('common.unknown')));
    }
  };

  const columns = [
    { title: t('common.name'), dataIndex: 'name', key: 'name' },
    { title: t('common.description'), dataIndex: 'description', key: 'description', ellipsis: true },
    { title: t('workflows.steps'), key: 'steps', render: (_: any, r: any) => r.steps?.length || 0 },
    { title: t('common.version'), dataIndex: 'version', key: 'version' },
    {
      title: t('common.actions'), key: 'actions', render: (_: any, record: any) => (
        <Space>
          <Button icon={<PlusOutlined />} size="small" onClick={() => { setSelectedWf(record); setEditingStep(null); stepForm.resetFields(); setStepModalOpen(true); }}>
            {t('workflows.addStep')}
          </Button>
          <Button icon={<DeleteOutlined />} size="small" danger onClick={() => handleDeleteWfClick(record.id)}>{t('common.delete')}</Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>{t('workflows.title')}</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setModalOpen(true); }}>
          {t('workflows.create')}
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
              header={<strong>{t('workflows.steps')}</strong>}
              dataSource={record.steps || []}
              renderItem={(step: any) => (
                <List.Item
                  actions={[
                    <Button size="small" icon={<EditOutlined />} onClick={() => openEditStep(record, step)}>{t('common.edit')}</Button>,
                    <Popconfirm title={t('workflows.deleteStepConfirm')} onConfirm={() => handleDeleteStep(record.id, step.id)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
                      <Button size="small" danger>{t('common.delete')}</Button>
                    </Popconfirm>,
                  ]}
                >
                  <List.Item.Meta
                    avatar={<Tag color="blue">{step.order}</Tag>}
                    title={`${step.name} (${step.step_type})`}
                    description={
                      <div>
                        <div>{step.prompt_template || t('workflows.noPromptTemplate')}</div>
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
                    {step.requires_human_confirm && <Tag color="orange">{t('workflows.requiresConfirmTag')}</Tag>}
                    {step.tool_id && <Tag color="purple">{t('workflows.toolBoundTag')}</Tag>}
                    <Tag>{step.on_failure}</Tag>
                  </Space>
                </List.Item>
              )}
            />
          ),
        }}
      />

      {/* Create workflow modal */}
      <Modal title={t('workflows.create')} open={modalOpen} onOk={handleCreateWf} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label={t('common.name')} rules={[{ required: true }]}>
            <Input placeholder={t('workflows.namePlaceholderExample')} />
          </Form.Item>
          <Form.Item name="description" label={t('common.description')}>
            <TextArea rows={2} placeholder={t('workflows.descriptionPlaceholder')} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Add/Edit step modal */}
      <Modal
        title={editingStep
          ? t('workflows.editStepModalTitle', { name: selectedWf?.name || '' })
          : t('workflows.addStepModalTitle', { name: selectedWf?.name || '' })}
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
          <Form.Item name="name" label={t('workflows.stepName')} rules={[{ required: true }]}>
            <Input placeholder={t('workflows.stepNamePlaceholder')} />
          </Form.Item>
          <Form.Item name="order" label={t('workflows.stepOrder')} rules={[{ required: true }]}>
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="step_type" label={t('workflows.stepType')}>
            <Select options={STEP_TYPES} />
          </Form.Item>
          <Form.Item name="prompt_template" label={t('workflows.promptTemplate')}>
            <TextArea rows={3} placeholder={t('workflows.promptTemplatePlaceholder')} />
          </Form.Item>
          <Form.Item name="on_failure" label={t('workflows.onFailure')}>
            <Select options={FAILURE_ACTIONS} />
          </Form.Item>
          <Form.Item name="max_retries" label={t('workflows.maxRetries')}>
            <InputNumber min={0} max={10} />
          </Form.Item>
          <Form.Item name="risk_level" label={t('workflows.riskLevel')}>
            <Select options={[
              { value: 'info', label: t('workflows.riskLevels.info') },
              { value: 'warning', label: t('workflows.riskLevels.warning') },
              { value: 'critical', label: t('workflows.riskLevels.critical') },
            ]} />
          </Form.Item>

          {/* ── collect: form fields editor ── */}
          {stepType === 'collect' && (
            <>
              <Divider orientation="left">{t('workflows.fieldsConfig')}</Divider>
              <Form.List name="fields">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map(({ key, name, ...restField }) => (
                      <Card
                        key={key}
                        size="small"
                        style={{ marginBottom: 8 }}
                        title={t('workflows.fieldNumber', { n: name + 1 })}
                        extra={<MinusCircleOutlined style={{ color: '#ff4d4f' }} onClick={() => remove(name)} />}
                      >
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                          <Form.Item
                            {...restField}
                            name={[name, 'name']}
                            label={t('workflows.fieldName')}
                            rules={[{ required: true, message: t('common.required') }]}
                            style={{ flex: 1, minWidth: 120 }}
                          >
                            <Input placeholder="field_name" />
                          </Form.Item>
                          <Form.Item
                            {...restField}
                            name={[name, 'label']}
                            label={t('workflows.fieldLabel')}
                            rules={[{ required: true, message: t('common.required') }]}
                            style={{ flex: 1, minWidth: 120 }}
                          >
                            <Input placeholder={t('workflows.fieldLabelPlaceholder')} />
                          </Form.Item>
                          <Form.Item
                            {...restField}
                            name={[name, 'field_type']}
                            label={t('workflows.fieldType')}
                            style={{ width: 110 }}
                          >
                            <Select options={FIELD_TYPES} />
                          </Form.Item>
                          <Form.Item
                            {...restField}
                            name={[name, 'required']}
                            label={t('workflows.fieldRequired')}
                            valuePropName="checked"
                          >
                            <Switch />
                          </Form.Item>
                        </div>
                        <Form.Item {...restField} name={[name, 'placeholder']} label={t('workflows.placeholderLabel')}>
                          <Input placeholder={t('workflows.placeholderPlaceholder')} />
                        </Form.Item>
                        {/* options: only for select type */}
                        <Form.Item shouldUpdate noStyle>
                          {({ getFieldValue }) => {
                            const ft = getFieldValue(['fields', name, 'field_type']);
                            if (ft !== 'select') return null;
                            return (
                              <Form.Item
                                name={[name, 'options']}
                                label={t('workflows.optionsLabel')}
                                extra={t('workflows.optionsExtra')}
                              >
                                <TextArea rows={2} placeholder='[{"label":"Option 1","value":"v1"}]' />
                              </Form.Item>
                            );
                          }}
                        </Form.Item>
                      </Card>
                    ))}
                    <Button type="dashed" onClick={() => add({ field_type: 'text', required: true })} block icon={<PlusOutlined />}>
                      {t('workflows.addField')}
                    </Button>
                  </>
                )}
              </Form.List>
            </>
          )}

          {/* ── tool_call: tool binding ── */}
          {stepType === 'tool_call' && (
            <>
              <Divider orientation="left">{t('workflows.toolBinding')}</Divider>
              <Form.Item name="tool_id" label={t('workflows.selectToolLabel')} rules={[{ required: true, message: t('workflows.toolRequiredMsg') }]}>
                <Select allowClear placeholder={t('workflows.selectToolPlaceholder')} options={toolOptions} />
              </Form.Item>
              <Form.Item shouldUpdate noStyle>
                {({ getFieldValue }) => {
                  const tid = getFieldValue('tool_id');
                  const tool = tools.find(tl => tl.id === tid);
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
              <Divider orientation="left">{t('workflows.confirmConfig')}</Divider>
              <Form.Item name="requires_human_confirm" label={t('workflows.requiresHumanConfirmLabel')} valuePropName="checked">
                <Switch />
              </Form.Item>
            </>
          )}

          {/* ── complete: webhook config ── */}
          {stepType === 'complete' && (
            <>
              <Divider orientation="left">{t('workflows.webhookConfig')}</Divider>
              <Form.Item name={['tool_config', 'webhook_enabled']} label={t('workflows.sendToExternalOnComplete')} valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item shouldUpdate noStyle>
                {({ getFieldValue }) => {
                  if (!getFieldValue(['tool_config', 'webhook_enabled'])) return null;
                  return (
                    <>
                      <Form.Item
                        name={['tool_config', 'webhook_url']}
                        label={t('workflows.webhookUrlLabel')}
                        rules={[{ required: true, message: t('workflows.webhookUrlRequiredMsg') }]}
                      >
                        <Input placeholder="https://example.com/webhook" />
                      </Form.Item>
                      <Form.Item name={['tool_config', 'webhook_method']} label={t('workflows.webhookMethodLabel')}>
                        <Select options={[
                          { value: 'POST', label: 'POST' },
                          { value: 'PUT', label: 'PUT' },
                          { value: 'PATCH', label: 'PATCH' },
                        ]} />
                      </Form.Item>
                      <Form.Item
                        name={['tool_config', 'webhook_headers']}
                        label={t('workflows.webhookHeadersLabel')}
                        extra={t('workflows.webhookHeadersExtra')}
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
