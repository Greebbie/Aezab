import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, InputNumber, Select, Switch, Space, message, Tag, Popconfirm, Alert } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import { skillApi, workflowApi, toolApi, knowledgeApi, agentApi } from '../api';

const SKILL_TYPE_OPTIONS = [
  { value: 'workflow', label: 'Workflow' },
  { value: 'tool_call', label: 'Tool Call' },
  { value: 'knowledge_qa', label: 'Knowledge QA' },
  { value: 'delegate', label: 'Delegate' },
  { value: 'composite', label: 'Composite' },
];

const SKILL_TYPE_COLORS: Record<string, string> = {
  workflow: 'blue',
  tool_call: 'purple',
  knowledge_qa: 'green',
  delegate: 'gold',
  composite: 'cyan',
};

export default function SkillsPage() {
  const [skills, setSkills] = useState<any[]>([]);
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [tools, setTools] = useState<any[]>([]);
  const [sources, setSources] = useState<any[]>([]);
  const [agents, setAgents] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [form] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      const [skillRes, wfRes, toolRes, sourceRes, agentRes] = await Promise.all([
        skillApi.list('default', 'null'),
        workflowApi.list(),
        toolApi.list(),
        knowledgeApi.listSources(),
        agentApi.list(),
      ]);
      setSkills(skillRes.data);
      setWorkflows(wfRes.data);
      setTools(toolRes.data);
      setSources(sourceRes.data);
      setAgents(agentRes.data);
    } catch (e: any) {
      message.error('Failed to load data: ' + (e.message || 'unknown'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const selectedType: string = Form.useWatch('skill_type', form) || '';

  const handleSave = async () => {
    try {
      const values = await form.validateFields();

      // Build execution_config based on skill_type
      let execution_config: any = {};
      switch (values.skill_type) {
        case 'workflow':
          execution_config = { workflow_id: values.exec_workflow_id };
          break;
        case 'tool_call':
          execution_config = {
            tool_ids: values.exec_tool_ids || [],
            function_calling_enabled: true,
            max_tool_rounds: values.exec_max_tool_rounds ?? 5,
          };
          break;
        case 'knowledge_qa':
          execution_config = {
            knowledge_source_ids: values.exec_knowledge_source_ids || [],
            domain: values.exec_domain || undefined,
          };
          break;
        case 'delegate':
          execution_config = {
            target_agent_id: values.exec_target_agent_id,
            context_fields: values.exec_context_fields || [],
          };
          break;
        case 'composite':
          execution_config = {
            sub_skill_ids: values.exec_sub_skill_ids || [],
            mode: 'sequential',
          };
          break;
      }

      const payload = {
        name: values.name,
        description: values.description || '',
        skill_type: values.skill_type,
        execution_config,
        enabled: values.enabled ?? true,
      };

      if (editing) {
        await skillApi.update(editing.id, payload);
        message.success('Skill updated');
      } else {
        await skillApi.create(payload);
        message.success('Skill created');
      }
      setModalOpen(false);
      setEditing(null);
      form.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error('Save failed: ' + (e.response?.data?.detail || e.message || 'unknown'));
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await skillApi.delete(id);
      message.success('Deleted');
      load();
    } catch (e: any) {
      message.error('Delete failed: ' + (e.response?.data?.detail || e.message || 'unknown'));
    }
  };

  const openEdit = (record: any) => {
    setEditing(record);
    const exec = record.execution_config || {};

    const formValues: any = {
      name: record.name,
      description: record.description,
      skill_type: record.skill_type,
      enabled: record.enabled,
    };

    // Set execution config fields based on type
    switch (record.skill_type) {
      case 'workflow':
        formValues.exec_workflow_id = exec.workflow_id;
        break;
      case 'tool_call':
        formValues.exec_tool_ids = exec.tool_ids || [];
        formValues.exec_max_tool_rounds = exec.max_tool_rounds ?? 5;
        break;
      case 'knowledge_qa':
        formValues.exec_knowledge_source_ids = exec.knowledge_source_ids || [];
        formValues.exec_domain = exec.domain || '';
        break;
      case 'delegate':
        formValues.exec_target_agent_id = exec.target_agent_id;
        formValues.exec_context_fields = exec.context_fields || [];
        break;
      case 'composite':
        formValues.exec_sub_skill_ids = exec.sub_skill_ids || [];
        break;
    }

    form.setFieldsValue(formValues);
    setModalOpen(true);
  };

  const columns = [
    { title: 'Name', dataIndex: 'name', key: 'name' },
    {
      title: 'Type', dataIndex: 'skill_type', key: 'skill_type',
      render: (v: string) => <Tag color={SKILL_TYPE_COLORS[v] || 'default'}>{v}</Tag>,
    },
    { title: 'Description', dataIndex: 'description', key: 'description', ellipsis: true },
    {
      title: 'Status', dataIndex: 'enabled', key: 'enabled',
      render: (v: boolean) => v ? <Tag color="green">Enabled</Tag> : <Tag color="red">Disabled</Tag>,
    },
    {
      title: 'Actions', key: 'actions',
      render: (_: any, record: any) => (
        <Space>
          <Button icon={<EditOutlined />} size="small" onClick={() => openEdit(record)}>Edit</Button>
          <Popconfirm title="Delete this skill?" onConfirm={() => handleDelete(record.id)} okText="Confirm" cancelText="Cancel">
            <Button icon={<DeleteOutlined />} size="small" danger>Delete</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h2 style={{ marginBottom: 4 }}>Advanced Skills</h2>
          <div style={{ color: '#64748b' }}>
            Skills are runtime capability wrappers for advanced orchestration, not the main setup path for agents.
          </div>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); form.resetFields(); setModalOpen(true); }}>
          Create Skill
        </Button>
      </div>
      <Alert
        message="Use Agent Management -> Capabilities for normal agent setup"
        description={
          <div>
            <div>
              Knowledge, Workflows, and Tools remain the source-of-truth resource pages. Agent Capabilities binds
              them to an agent and automatically creates managed skills for runtime function calling.
            </div>
            <ul style={{ margin: '8px 0 0 18px', padding: 0 }}>
              <li>Reusable capability groups shared across multiple agents.</li>
              <li>Composite skills that package multiple capabilities into one macro capability.</li>
              <li>Delegate skills for routing a user to another agent, such as a different region or team.</li>
              <li>debug runtime wrappers to inspect how capabilities are exposed to the LLM.</li>
            </ul>
            <div style={{ marginTop: 8 }}>
              Auto-managed skills created via Agent Capabilities are hidden here by default.
            </div>
          </div>
        }
        type="info"
        showIcon
        closable
        style={{ marginBottom: 16 }}
      />
      <Table dataSource={skills} columns={columns} rowKey="id" loading={loading} />

      <Modal
        title={editing ? 'Edit Skill' : 'Create Skill'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={720}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          {/* Basic info */}
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input placeholder="e.g. Weather Lookup, Document QA" />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input placeholder="Brief description of the skill" />
          </Form.Item>
          <Form.Item name="skill_type" label="Type" rules={[{ required: true }]}>
            <Select options={SKILL_TYPE_OPTIONS} placeholder="Select skill type" />
          </Form.Item>

          {/* Dynamic execution config based on skill_type */}
          {selectedType === 'workflow' && (
            <Form.Item name="exec_workflow_id" label="Workflow" rules={[{ required: true, message: 'Please select a workflow' }]}>
              <Select placeholder="Select workflow"
                options={workflows.map((w: any) => ({ value: w.id, label: `${w.name} (${w.id.slice(0, 8)})` }))}
              />
            </Form.Item>
          )}

          {selectedType === 'tool_call' && (
            <>
              <Form.Item name="exec_tool_ids" label="Tools" rules={[{ required: true, message: 'Please select tools' }]}>
                <Select mode="multiple" placeholder="Select tools"
                  options={tools.map((t: any) => ({ value: t.id, label: `${t.name} (${t.category})` }))}
                />
              </Form.Item>
              <Form.Item name="exec_max_tool_rounds" label="Max Tool Rounds" initialValue={5}>
                <InputNumber min={1} max={20} />
              </Form.Item>
            </>
          )}

          {selectedType === 'knowledge_qa' && (
            <>
              <Form.Item name="exec_knowledge_source_ids" label="Knowledge Sources">
                <Select mode="multiple" placeholder="Select knowledge sources"
                  options={sources.map((s: any) => ({ value: s.id, label: `${s.name} (${s.domain})` }))}
                />
              </Form.Item>
              <Form.Item name="exec_domain" label="Domain">
                <Input placeholder="Knowledge domain identifier" />
              </Form.Item>
            </>
          )}

          {selectedType === 'delegate' && (
            <>
              <Form.Item name="exec_target_agent_id" label="Target Agent" rules={[{ required: true, message: 'Please select a target agent' }]}>
                <Select placeholder="Select target agent"
                  options={agents.map((a: any) => ({ value: a.id, label: `${a.name} (${a.id.slice(0, 8)})` }))}
                />
              </Form.Item>
              <Form.Item name="exec_context_fields" label="Shared Context Fields">
                <Select mode="tags" placeholder="Enter field names, press Enter" tokenSeparators={[',']} />
              </Form.Item>
            </>
          )}

          {selectedType === 'composite' && (
            <Form.Item name="exec_sub_skill_ids" label="Sub-Skills" rules={[{ required: true, message: 'Please select sub-skills' }]}>
              <Select mode="multiple" placeholder="Select sub-skills (executed sequentially)"
                options={skills.filter((s: any) => !editing || s.id !== editing.id).map((s: any) => ({
                  value: s.id,
                  label: `${s.name} (${s.skill_type})`,
                }))}
              />
            </Form.Item>
          )}

          <Form.Item name="enabled" label="Enabled" valuePropName="checked" initialValue={true}>
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
