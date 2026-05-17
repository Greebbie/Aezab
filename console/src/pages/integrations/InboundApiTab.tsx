import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Card, Descriptions, Empty, Input, Select, Space, Steps, Tabs, message } from 'antd';
import { ExperimentOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { invokeApi } from '../../api';
import type { Agent, InvokeResponse } from '../../types';
import type { IntegrationCopy } from './copy';
import IntegrationCodeBlock from './IntegrationCodeBlock';
import {
  buildAsrSnippet,
  buildCurlInvoke,
  buildInvokePayload,
  buildJsInvoke,
  buildSseSnippet,
  buildWorkflowFormSubmitPayload,
  formatJson,
  responseContract,
  workflowCardContract,
} from './snippets';

const { TextArea } = Input;

interface Props {
  apiBase: string;
  agents: Agent[];
  selectedAgentId: string;
  onSelectAgent: (agentId: string) => void;
  copy: IntegrationCopy;
}

export default function InboundApiTab({ apiBase, agents, selectedAgentId, onSelectAgent, copy }: Props) {
  const navigate = useNavigate();
  const [testMessage, setTestMessage] = useState('I need to create a repair ticket');
  const [testResult, setTestResult] = useState<InvokeResponse | null>(null);
  const [testing, setTesting] = useState(false);

  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId);

  useEffect(() => {
    const firstAgentId = agents[0]?.id || '';

    if (!selectedAgentId) {
      if (firstAgentId) {
        onSelectAgent(firstAgentId);
      }
      return;
    }

    if (!agents.some((agent) => agent.id === selectedAgentId)) {
      onSelectAgent(firstAgentId);
    }
  }, [agents, onSelectAgent, selectedAgentId]);

  const snippets = useMemo(
    () => [
      { key: 'curl', label: 'curl Invoke', value: buildCurlInvoke(apiBase, selectedAgentId) },
      { key: 'js', label: 'JavaScript', value: buildJsInvoke(apiBase, selectedAgentId) },
      { key: 'sse', label: 'SSE Stream', value: buildSseSnippet(apiBase, selectedAgentId) },
      { key: 'asr', label: 'ASR Upload', value: buildAsrSnippet(apiBase) },
    ],
    [apiBase, selectedAgentId],
  );

  const runTest = async () => {
    if (!selectedAgentId) {
      message.warning(copy.messages.selectAgentFirst);
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await invokeApi.send({
        ...buildInvokePayload(selectedAgentId, testMessage),
      });
      setTestResult(res.data);
    } catch (error) {
      message.error(error instanceof Error ? error.message : copy.messages.invokeFailed);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div>
      <Card title={copy.fields.selectAgent} style={{ marginBottom: 16 }}>
        <Select
          style={{ width: '100%' }}
          value={selectedAgentId || undefined}
          placeholder={copy.empty.noAgent}
          onChange={onSelectAgent}
          options={agents.map((agent) => ({ value: agent.id, label: `${agent.name} (${agent.id})` }))}
        />
        {selectedAgent ? (
          <Descriptions size="small" column={1} style={{ marginTop: 12 }}>
            <Descriptions.Item label="Agent ID">{selectedAgent.id}</Descriptions.Item>
            <Descriptions.Item label={copy.fields.description}>{selectedAgent.description || '-'}</Descriptions.Item>
          </Descriptions>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={copy.empty.noAgent} />
        )}
      </Card>

      <Card title={copy.sectionTitles.liveInvokeTest} style={{ marginBottom: 16 }}>
        <TextArea
          rows={4}
          value={testMessage}
          onChange={(event) => setTestMessage(event.target.value)}
          placeholder="Send a test message through the same API customer apps use"
        />
        <Space style={{ marginTop: 12 }} wrap>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={runTest} loading={testing}>
            {copy.actions.sendTest || 'Send Test'}
          </Button>
          <Button icon={<ExperimentOutlined />} onClick={() => navigate('/playground')}>
            {copy.actions.openPlayground}
          </Button>
        </Space>
        {testResult && (
          <div style={{ marginTop: 16 }}>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label={copy.fields.answer}>{testResult.short_answer}</Descriptions.Item>
              <Descriptions.Item label={copy.fields.session}>{testResult.session_id}</Descriptions.Item>
              <Descriptions.Item label={copy.fields.trace}>{testResult.trace_id}</Descriptions.Item>
              <Descriptions.Item label={copy.fields.workflow}>{testResult.workflow_status || '-'}</Descriptions.Item>
            </Descriptions>
            <IntegrationCodeBlock value={formatJson(testResult)} copy={copy} />
          </div>
        )}
      </Card>

      <Card title={copy.customerAppFlow.title || 'Customer App Integration Flow'} style={{ marginBottom: 16 }}>
        <Alert type="info" showIcon message={copy.customerAppFlow.description} style={{ marginBottom: 16 }} />
        <Steps
          direction="vertical"
          size="small"
          items={copy.customerAppFlow.steps.map((step) => ({ title: step }))}
          style={{ marginBottom: 16 }}
        />
        <Tabs
          items={[
            {
              key: 'workflow-card',
              label: copy.customerAppFlow.workflowCardTitle,
              children: <IntegrationCodeBlock value={formatJson(workflowCardContract)} copy={copy} />,
            },
            {
              key: 'form-submit',
              label: copy.customerAppFlow.formSubmitTitle,
              children: (
                <IntegrationCodeBlock value={formatJson(buildWorkflowFormSubmitPayload(selectedAgentId))} copy={copy} />
              ),
            },
          ]}
        />
      </Card>

      <Card title={copy.sectionTitles.clientSnippets} style={{ marginBottom: 16 }}>
        <Tabs
          items={snippets.map((snippet) => ({
            key: snippet.key,
            label: snippet.label,
            children: <IntegrationCodeBlock value={snippet.value} copy={copy} />,
          }))}
        />
      </Card>

      <Card title={copy.sectionTitles.responseContract}>
        <IntegrationCodeBlock value={formatJson(responseContract)} copy={copy} />
      </Card>
    </div>
  );
}
