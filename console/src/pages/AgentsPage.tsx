import { useEffect, useState, useCallback } from 'react';
import {
  Table, Button, Modal, Form, Input, Switch, Space, message, Tag,
  Popconfirm, Select, Collapse, Typography, Tabs, Card, Divider, Alert, Upload,
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined, MinusCircleOutlined, ThunderboltOutlined,
  DownloadOutlined, UploadOutlined,
} from '@ant-design/icons';
import {
  agentApi, agentCapabilitiesApi, agentConnectionApi,
  workflowApi, toolApi, knowledgeApi, llmConfigApi,
} from '../api';
import { AgentWizard, TemplateStampModal } from '../components/agent';
import { HelpLabel, HelpTooltip } from '../components/shared';
import { useTranslation } from 'react-i18next';
import { friendlyError } from '../utils/friendlyError';

const { TextArea } = Input;
const { Text } = Typography;

interface KnowledgeCap { domain: string; source_ids: string[]; keywords: string[]; description: string }
interface WorkflowCap { workflow_id: string; keywords: string[]; description: string }
interface ToolCap { tool_ids: string[]; keywords: string[]; description: string }
interface Capabilities { knowledge: KnowledgeCap[]; workflows: WorkflowCap[]; tools: ToolCap[] }

const emptyCaps = (): Capabilities => ({ knowledge: [], workflows: [], tools: [] });

export default function AgentsPage() {
  const { t } = useTranslation();
  const [agents, setAgents] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [templateModalOpen, setTemplateModalOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [activeTab, setActiveTab] = useState('basic');
  const [form] = Form.useForm();

  // Capabilities state
  const [capabilities, setCapabilities] = useState<Capabilities>(emptyCaps());
  const [capLoading, setCapLoading] = useState(false);
  const [capSaving, setCapSaving] = useState(false);

  // Reference data for dropdowns
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [tools, setTools] = useState<any[]>([]);
  const [sources, setSources] = useState<any[]>([]);
  const [llmConfigs, setLlmConfigs] = useState<any[]>([]);

  // Agent connection state
  const [connections, setConnections] = useState<any[]>([]);
  const [connTarget, setConnTarget] = useState<string | undefined>(undefined);
  const [connType, setConnType] = useState<string>('delegate');
  const [connDesc, setConnDesc] = useState('');

  const loadAgents = useCallback(async () => {
    setLoading(true);
    try {
      const [agentRes, llmRes] = await Promise.all([
        agentApi.list(),
        llmConfigApi.list(),
      ]);
      setAgents(agentRes.data);
      setLlmConfigs(llmRes.data);
    } catch (e) {
      message.error(`Failed to load agents: ${friendlyError(e, t)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAgents(); }, [loadAgents]);

  // Load reference data + capabilities + connections when editing
  const loadAgentDetails = useCallback(async (agentId: string) => {
    setCapLoading(true);
    try {
      const [capRes, connRes, wfRes, toolRes, srcRes, llmRes] = await Promise.all([
        agentCapabilitiesApi.get(agentId),
        agentConnectionApi.list('default', agentId),
        workflowApi.list(),
        toolApi.list(),
        knowledgeApi.listSources(),
        llmConfigApi.list(),
      ]);
      setCapabilities(capRes.data);
      setConnections(connRes.data);
      setWorkflows(wfRes.data);
      setTools(toolRes.data);
      setSources(srcRes.data);
      setLlmConfigs(llmRes.data);
    } catch (e) {
      message.error(`Failed to load agent details: ${friendlyError(e, t)}`);
    } finally {
      setCapLoading(false);
    }
  }, []);

  // Load reference data for create mode (no capabilities yet)
  const loadRefData = useCallback(async () => {
    try {
      const [wfRes, toolRes, srcRes, llmRes] = await Promise.all([
        workflowApi.list(),
        toolApi.list(),
        knowledgeApi.listSources(),
        llmConfigApi.list(),
      ]);
      setWorkflows(wfRes.data);
      setTools(toolRes.data);
      setSources(srcRes.data);
      setLlmConfigs(llmRes.data);
    } catch {
      // non-critical
    }
  }, []);

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const payload: any = {
        name: values.name,
        description: values.description || '',
        system_prompt: values.system_prompt || '',
        llm_config_id: values.llm_config_id || null,
        llm_model: values.llm_config_id ? null : (values.llm_model || null),
        enabled: values.enabled ?? true,
      };

      if (editing) {
        await agentApi.update(editing.id, payload);
        message.success('Agent updated');
      } else {
        const res = await agentApi.create(payload);
        message.success('Agent created — you can now configure capabilities');
        const created = res.data;
        setEditing(created);
        form.setFieldsValue(created);
        setActiveTab('capabilities');
        await loadAgentDetails(created.id);
        loadAgents();
        return; // keep modal open for capabilities
      }
      setModalOpen(false);
      setEditing(null);
      form.resetFields();
      loadAgents();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(`Save failed: ${friendlyError(e, t)}`);
    }
  };

  const handleSaveCapabilities = async () => {
    if (!editing) return;
    setCapSaving(true);
    try {
      const res = await agentCapabilitiesApi.update(editing.id, capabilities);
      setCapabilities(res.data);
      message.success('Capabilities saved');
    } catch (e) {
      message.error(`Save capabilities failed: ${friendlyError(e, t)}`);
    } finally {
      setCapSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await agentApi.delete(id);
      message.success('Deleted');
      loadAgents();
    } catch (e) {
      message.error(`Delete failed: ${friendlyError(e, t)}`);
    }
  };

  const handleExport = async (record: { id: string; name: string }) => {
    try {
      const res = await agentApi.export(record.id);
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${record.name}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      message.error(`Export failed: ${friendlyError(e, t)}`);
    }
  };

  const handleImportFile = (file: File): boolean => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const text = typeof reader.result === 'string' ? reader.result : '';
        const data = JSON.parse(text) as Record<string, unknown>;
        await agentApi.import(data);
        message.success('Agent imported');
        loadAgents();
      } catch (e) {
        message.error(`Import failed: ${friendlyError(e, t)}`);
      }
    };
    reader.readAsText(file);
    return false;
  };

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    setCapabilities(emptyCaps());
    setConnections([]);
    setConnTarget(undefined);
    setConnType('delegate');
    setConnDesc('');
    setActiveTab('basic');
    setModalOpen(true);
    loadRefData();
  };

  const openEdit = async (record: any) => {
    setEditing(record);
    form.setFieldsValue({
      name: record.name,
      description: record.description,
      system_prompt: record.system_prompt,
      llm_config_id: record.llm_config_id || undefined,
      llm_model: record.llm_model,
      enabled: record.enabled,
    });
    setConnTarget(undefined);
    setConnType('delegate');
    setConnDesc('');
    setActiveTab('basic');
    setModalOpen(true);
    await loadAgentDetails(record.id);
  };

  // ── Agent connections ──

  const handleAddConnection = async () => {
    if (!editing || !connTarget) return;
    try {
      await agentConnectionApi.create({
        source_agent_id: editing.id,
        target_agent_id: connTarget,
        connection_type: connType,
        description: connDesc,
      });
      message.success('Connection added');
      setConnTarget(undefined);
      setConnDesc('');
      const connRes = await agentConnectionApi.list('default', editing.id);
      setConnections(connRes.data);
    } catch (e) {
      message.error(`Add connection failed: ${friendlyError(e, t)}`);
    }
  };

  const handleDeleteConnection = async (connId: string) => {
    try {
      await agentConnectionApi.delete(connId);
      message.success('Connection removed');
      if (editing) {
        const connRes = await agentConnectionApi.list('default', editing.id);
        setConnections(connRes.data);
      }
    } catch (e) {
      message.error(`Delete connection failed: ${friendlyError(e, t)}`);
    }
  };

  // ── Capabilities helpers ──

  const addKnowledge = () => setCapabilities(prev => ({
    ...prev,
    knowledge: [...prev.knowledge, { domain: 'default', source_ids: [], keywords: [], description: '' }],
  }));
  const removeKnowledge = (idx: number) => setCapabilities(prev => ({
    ...prev,
    knowledge: prev.knowledge.filter((_, i) => i !== idx),
  }));
  const updateKnowledge = (idx: number, field: keyof KnowledgeCap, value: any) =>
    setCapabilities(prev => ({
      ...prev,
      knowledge: prev.knowledge.map((k, i) => i === idx ? { ...k, [field]: value } : k),
    }));

  const addWorkflow = () => setCapabilities(prev => ({
    ...prev,
    workflows: [...prev.workflows, { workflow_id: '', keywords: [], description: '' }],
  }));
  const removeWorkflow = (idx: number) => setCapabilities(prev => ({
    ...prev,
    workflows: prev.workflows.filter((_, i) => i !== idx),
  }));
  const updateWorkflow = (idx: number, field: keyof WorkflowCap, value: any) =>
    setCapabilities(prev => ({
      ...prev,
      workflows: prev.workflows.map((w, i) => i === idx ? { ...w, [field]: value } : w),
    }));

  const addTool = () => setCapabilities(prev => ({
    ...prev,
    tools: [...prev.tools, { tool_ids: [], keywords: [], description: '' }],
  }));
  const removeTool = (idx: number) => setCapabilities(prev => ({
    ...prev,
    tools: prev.tools.filter((_, i) => i !== idx),
  }));
  const updateTool = (idx: number, field: keyof ToolCap, value: any) =>
    setCapabilities(prev => ({
      ...prev,
      tools: prev.tools.map((t, i) => i === idx ? { ...t, [field]: value } : t),
    }));

  // ── Computed ──
  const renderAdvancedInstruction = (
    value: string,
    onChange: (value: string) => void,
    placeholder: string,
  ) => (
    <Collapse
      ghost
      size="small"
      items={[
        {
          key: 'advanced',
          label: (
            <HelpLabel
              label="Agent-specific instruction (advanced)"
              help={t('agents.help.advancedInstruction')}
            />
          ),
          children: (
            <Space direction="vertical" style={{ width: '100%' }} size={6}>
              <Text type="secondary">
                Normally leave this empty. Use it only when this Agent should use the bound resource differently from its own description.
              </Text>
              <TextArea
                rows={2}
                placeholder={placeholder}
                value={value}
                onChange={e => onChange(e.target.value)}
              />
            </Space>
          ),
        },
      ]}
    />
  );

  const otherAgents = agents.filter(a => a.id !== editing?.id);

  // ── Table columns ──
  const columns = [
    { title: 'Name', dataIndex: 'name', key: 'name' },
    { title: 'Description', dataIndex: 'description', key: 'description', ellipsis: true },
    {
      title: 'LLM', key: 'llm',
      render: (_: any, record: any) => {
        if (record.llm_config_id) {
          const cfg = llmConfigs.find((c: any) => c.id === record.llm_config_id);
          return cfg ? <Tag color="blue">{cfg.name}</Tag> : record.llm_config_id.slice(0, 8);
        }
        return record.llm_model || 'Default';
      },
    },
    {
      title: 'Status', dataIndex: 'enabled', key: 'enabled',
      render: (v: boolean) => v ? <Tag color="green">Enabled</Tag> : <Tag color="red">Disabled</Tag>,
    },
    { title: 'Version', dataIndex: 'version', key: 'version' },
    {
      title: 'Actions', key: 'actions', render: (_: any, record: any) => (
        <Space>
          <Button icon={<EditOutlined />} size="small" onClick={() => openEdit(record)}>Edit</Button>
          <Button icon={<DownloadOutlined />} size="small" onClick={() => handleExport(record)}>Export</Button>
          <Popconfirm title="确定删除此智能体及其自动管理的技能？" onConfirm={() => handleDelete(record.id)} okText="Confirm" cancelText="Cancel">
            <Button icon={<DeleteOutlined />} size="small" danger>Delete</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── Connection table columns ──
  const connColumns = [
    {
      title: 'Target Agent', key: 'target',
      render: (_: any, r: any) => {
        const targetId = r.source_agent_id === editing?.id ? r.target_agent_id : r.source_agent_id;
        const agent = agents.find(a => a.id === targetId);
        return agent?.name || targetId;
      },
    },
    {
      title: 'Type', dataIndex: 'connection_type', key: 'type',
      render: (v: string) => <Tag>{v}</Tag>,
    },
    { title: 'Description', dataIndex: 'description', key: 'desc', ellipsis: true },
    {
      title: 'Actions', key: 'actions',
      render: (_: any, record: any) => (
        <Popconfirm title="确定移除此连接？" onConfirm={() => handleDeleteConnection(record.id)} okText="Confirm" cancelText="Cancel">
          <Button icon={<DeleteOutlined />} size="small" danger>Remove</Button>
        </Popconfirm>
      ),
    },
  ];

  // ── Tab 1: Basic Info ──
  const basicTab = (
    <Form form={form} layout="vertical">
      <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Name is required' }]}>
        <Input placeholder="e.g. Community Service Assistant" />
      </Form.Item>
      <Form.Item name="description" label="Description">
        <Input placeholder="Brief description of the agent" />
      </Form.Item>
      <Form.Item name="system_prompt" label="System Prompt">
        <TextArea rows={4} placeholder="You are an intelligent assistant for..." />
      </Form.Item>
      <Form.Item name="llm_config_id" label="LLM Configuration">
        <Select
          placeholder="Select a saved LLM config (or leave blank for default)"
          allowClear
          showSearch
          optionFilterProp="label"
          options={llmConfigs.map((c: any) => ({
            value: c.id,
            label: `${c.name} (${c.provider} / ${c.model})`,
          }))}
        />
      </Form.Item>
      <Form.Item name="llm_model" label="LLM Model Override" help="Only used when no LLM Configuration is selected above">
        <Input placeholder="qwen2.5 / glm-4 / deepseek-chat" />
      </Form.Item>
      <Form.Item name="enabled" label="Enabled" valuePropName="checked" initialValue={true}>
        <Switch />
      </Form.Item>
    </Form>
  );

  // ── Tab 2: Capabilities ──
  const capabilitiesTab = !editing ? (
    <div style={{ padding: '24px 0', textAlign: 'center' }}>
      <Text type="secondary">Please create the agent first (Basic Info tab), then configure capabilities.</Text>
    </div>
  ) : (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={(
          <>
            Agent capability source of truth
            <HelpTooltip content={t('agents.help.capabilitySource')} />
          </>
        )}
        description="Use this tab to decide what the agent can access at runtime. Resource descriptions drive when the Agent calls a capability; advanced Agent-specific instructions only add special rules for this Agent. Integrations is the developer workbench, and shortcut bindings write back here."
      />

      {/* Knowledge QA */}
      <Card
        size="small"
        title={<HelpLabel label="知识问答" help={t('agents.help.knowledgeCap')} />}
        extra={<Button size="small" icon={<PlusOutlined />} onClick={addKnowledge}>Add</Button>}
        style={{ marginBottom: 16 }}
      >
        {capabilities.knowledge.length === 0 && (
          <Text type="secondary">暂无知识绑定，点击"添加"绑定知识域。</Text>
        )}
        {capabilities.knowledge.map((k, idx) => (
          <div key={idx} style={{ marginBottom: 12, padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
              <Select
                mode="multiple"
                style={{ flex: 1, minWidth: 200 }}
                placeholder="选择知识源"
                value={k.source_ids}
                onChange={v => {
                  updateKnowledge(idx, 'source_ids', v);
                  // Auto-infer domain from first selected source
                  if (v.length > 0) {
                    const firstSource = sources.find((s: any) => s.id === v[0]);
                    if (firstSource) updateKnowledge(idx, 'domain', firstSource.domain);
                  }
                }}
                options={sources.map((s: any) => ({ value: s.id, label: `${s.name} (${s.domain})` }))}
                optionFilterProp="label"
              />
              <Button icon={<MinusCircleOutlined />} danger size="small" onClick={() => removeKnowledge(idx)} />
            </div>
            <Space direction="vertical" style={{ width: '100%', marginBottom: 8 }} size={4}>
              <Text type="secondary">
                <HelpLabel label="Resource description" help={t('agents.help.resourceDescription')} />
              </Text>
              {k.source_ids.length > 0 ? (
                sources
                  .filter((source: any) => k.source_ids.includes(source.id))
                  .map((source: any) => (
                    <Text key={source.id} type="secondary">
                      {source.name} ({source.domain}) - RAG searches this source's chunks automatically.
                    </Text>
                  ))
              ) : (
                <Text type="secondary">Select knowledge sources; RAG will use their indexed content directly.</Text>
              )}
            </Space>
            {renderAdvancedInstruction(
              k.description,
              value => updateKnowledge(idx, 'description', value),
              'Example: For this Agent, only use these sources for policy questions.',
            )}
          </div>
        ))}
      </Card>

      {/* Workflows */}
      <Card
        size="small"
        title={<HelpLabel label="工作流" help={t('agents.help.workflowCap')} />}
        extra={<Button size="small" icon={<PlusOutlined />} onClick={addWorkflow}>Add</Button>}
        style={{ marginBottom: 16 }}
      >
        {capabilities.workflows.length === 0 && (
          <Text type="secondary">暂无工作流绑定，点击"添加"绑定工作流。</Text>
        )}
        {capabilities.workflows.map((w, idx) => (
          <div key={idx} style={{ marginBottom: 12, padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
              <Select
                style={{ flex: 1 }}
                placeholder="选择工作流"
                value={w.workflow_id || undefined}
                onChange={v => updateWorkflow(idx, 'workflow_id', v)}
                options={workflows.map((wf: any) => ({ value: wf.id, label: `${wf.name}` }))}
                showSearch
                optionFilterProp="label"
              />
              <Button icon={<MinusCircleOutlined />} danger size="small" onClick={() => removeWorkflow(idx)} />
            </div>
            {(() => {
              const workflow = workflows.find((wf: any) => wf.id === w.workflow_id);
              return (
                <Space direction="vertical" style={{ width: '100%', marginBottom: 8 }} size={4}>
                  <Text type="secondary">
                    <HelpLabel label="Resource description" help={t('agents.help.resourceDescription')} />
                  </Text>
                  <Text type="secondary">
                    {workflow?.description || workflow?.name || 'Select a workflow; its name, description, and steps tell the Agent when to start it.'}
                  </Text>
                </Space>
              );
            })()}
            {renderAdvancedInstruction(
              w.description,
              value => updateWorkflow(idx, 'description', value),
              'Example: For this Agent, start this workflow only for paid customers.',
            )}
          </div>
        ))}
      </Card>

      {/* Tool Calling */}
      <Card
        size="small"
        title={<HelpLabel label="工具调用" help={t('agents.help.toolCap')} />}
        extra={<Button size="small" icon={<PlusOutlined />} onClick={addTool}>Add</Button>}
        style={{ marginBottom: 16 }}
      >
        {capabilities.tools.length === 0 && (
          <Text type="secondary">暂无工具绑定，点击"添加"绑定工具。</Text>
        )}
        {capabilities.tools.map((toolCap, idx) => (
          <div key={idx} style={{ marginBottom: 12, padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
              <Select
                mode="multiple"
                style={{ flex: 1 }}
                placeholder="Select tools"
                value={toolCap.tool_ids}
                onChange={v => updateTool(idx, 'tool_ids', v)}
                options={tools.map((tl: any) => ({ value: tl.id, label: `${tl.name}` }))}
                optionFilterProp="label"
              />
              <Button icon={<MinusCircleOutlined />} danger size="small" onClick={() => removeTool(idx)} />
            </div>
            <Space direction="vertical" style={{ width: '100%', marginBottom: 8 }} size={4}>
              <Text type="secondary">
                <HelpLabel label="Resource description" help={t('agents.help.resourceDescription')} />
              </Text>
              {toolCap.tool_ids.length > 0 ? (
                tools
                  .filter((tool: any) => toolCap.tool_ids.includes(tool.id))
                  .map((tool: any) => (
                    <Text key={tool.id} type="secondary">
                      {tool.name} - {tool.description || 'No description yet; add one in Tools so the Agent knows when to call it.'}
                    </Text>
                  ))
              ) : (
                <Text type="secondary">Select tools; each tool's name, description, and input schema are exposed to function calling individually.</Text>
              )}
            </Space>
            {renderAdvancedInstruction(
              toolCap.description,
              value => updateTool(idx, 'description', value),
              'Example: For this Agent, call these tools only after the user confirms the action.',
            )}
          </div>
        ))}
      </Card>

      <Button type="primary" onClick={handleSaveCapabilities} loading={capSaving}>
        Save Capabilities
      </Button>
    </div>
  );

  // ── Tab 3: Advanced ──
  const advancedTab = !editing ? (
    <div style={{ padding: '24px 0', textAlign: 'center' }}>
      <Text type="secondary">Please create the agent first.</Text>
    </div>
  ) : (
    <div>
      {/* Agent Connections */}
      <Collapse
        defaultActiveKey={['connections']}
        items={[{
          key: 'connections',
          label: `Agent Connections (${connections.length})`,
          children: (
            <>
              <Table
                dataSource={connections}
                columns={connColumns}
                rowKey="id"
                size="small"
                pagination={false}
                locale={{ emptyText: 'No connections.' }}
              />
              <Space style={{ marginTop: 12 }} wrap>
                <Select
                  style={{ width: 200 }}
                  placeholder="Target agent"
                  value={connTarget}
                  onChange={setConnTarget}
                  options={otherAgents.map(a => ({ value: a.id, label: a.name }))}
                  allowClear
                  showSearch
                  optionFilterProp="label"
                />
                <Select
                  style={{ width: 120 }}
                  value={connType}
                  onChange={setConnType}
                  options={[
                    { value: 'delegate', label: 'Delegate' },
                    { value: 'orchestrate', label: 'Orchestrate' },
                    { value: 'peer', label: 'Peer' },
                  ]}
                />
                <Input
                  style={{ width: 180 }}
                  placeholder="Description"
                  value={connDesc}
                  onChange={e => setConnDesc(e.target.value)}
                />
                <Button
                  icon={<PlusOutlined />}
                  onClick={handleAddConnection}
                  disabled={!connTarget}
                >
                  Add
                </Button>
              </Space>
            </>
          ),
        }]}
      />

      <Divider />

      <Text type="secondary">
        Need fine-grained control? Manage skills directly on the{' '}
        <a href="/skills">Skills page</a>.
      </Text>
    </div>
  );

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>Agent Management</h2>
        <Space>
          <Upload accept=".json" showUploadList={false} beforeUpload={handleImportFile}>
            <Button icon={<UploadOutlined />}>Import</Button>
          </Upload>
          <Button icon={<PlusOutlined />} onClick={() => setWizardOpen(true)}>
            Create from Scratch
          </Button>
          <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => setTemplateModalOpen(true)}>
            {t('agentTemplates.fromTemplate')}
          </Button>
        </Space>
      </div>
      <Table dataSource={agents} columns={columns} rowKey="id" loading={loading} />

      <Modal
        title={editing ? `Edit Agent: ${editing.name}` : 'Create Agent'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => { setModalOpen(false); setEditing(null); }}
        width={800}
        destroyOnClose
        okText={editing ? 'Save Basic Info' : 'Create'}
      >
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            { key: 'basic', label: 'Basic Info', children: basicTab },
            { key: 'capabilities', label: 'Capabilities', children: capabilitiesTab, disabled: !editing },
            { key: 'advanced', label: 'Advanced', children: advancedTab, disabled: !editing },
          ]}
        />
      </Modal>

      <AgentWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCreated={loadAgents}
        onSwitchToTemplate={() => { setWizardOpen(false); setTemplateModalOpen(true); }}
      />

      <TemplateStampModal
        open={templateModalOpen}
        onClose={() => setTemplateModalOpen(false)}
        onCreated={loadAgents}
      />
    </div>
  );
}
