import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Card, Col, Descriptions, Row, Space, Spin, Tabs, Typography } from 'antd';
import { ApiOutlined, AudioOutlined, AuditOutlined, LinkOutlined, ReloadOutlined, SendOutlined } from '@ant-design/icons';
import { getIntegrationCopy } from './integrations/copy';
import InboundApiTab from './integrations/InboundApiTab';
import OutboundToolsTab from './integrations/OutboundToolsTab';
import TraceDebugTab from './integrations/TraceDebugTab';
import { useIntegrationData } from './integrations/useIntegrationData';
import WorkflowWebhooksTab from './integrations/WorkflowWebhooksTab';

const { Paragraph, Text, Title } = Typography;

function getApiBase() {
  if (typeof window === 'undefined') return 'http://localhost:8000/api/v1';
  return `${window.location.origin}/api/v1`;
}

export default function IntegrationsPage() {
  const navigate = useNavigate();
  const { i18n } = useTranslation();
  const isZh = i18n.language?.startsWith('zh');
  const copy = useMemo(() => getIntegrationCopy(isZh), [isZh]);
  const apiBase = useMemo(() => getApiBase(), []);
  const [activeTab, setActiveTab] = useState('inbound');
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [hasLoadedData, setHasLoadedData] = useState(false);
  const { agents, tools, workflows, recentTraces, asrStatus, loading, error, reload } = useIntegrationData();

  const enabledTools = useMemo(() => tools.filter((tool) => tool.enabled), [tools]);
  const webhookWorkflows = useMemo(
    () =>
      workflows.filter((workflow) =>
        workflow.steps?.some((step) => step.step_type === 'complete' && step.tool_config?.webhook_enabled),
      ),
    [workflows],
  );
  const integrationCards = [
    {
      key: 'inbound',
      tabKey: 'inbound',
      icon: <SendOutlined style={{ fontSize: 22, color: '#1677ff' }} />,
      title: copy.tabs.inbound,
      metric: '/invoke, /invoke/stream, /asr/transcribe',
      help: copy.categoryHelp.inbound,
    },
    {
      key: 'asr',
      tabKey: 'inbound',
      icon: <AudioOutlined style={{ fontSize: 22, color: '#13c2c2' }} />,
      title: 'ASR',
      metric: `${String(asrStatus?.provider || 'not configured')} / ${String(asrStatus?.api_key_source || 'none')}`,
      help: copy.categoryHelp.asr,
    },
    {
      key: 'outbound',
      tabKey: 'outbound',
      icon: <ApiOutlined style={{ fontSize: 22, color: '#52c41a' }} />,
      title: copy.tabs.outbound,
      metric: `${enabledTools.length} enabled tools`,
      help: copy.categoryHelp.outbound,
    },
    {
      key: 'webhooks',
      tabKey: 'webhooks',
      icon: <LinkOutlined style={{ fontSize: 22, color: '#fa8c16' }} />,
      title: copy.tabs.webhooks,
      metric: `${webhookWorkflows.length} webhook workflows`,
      help: copy.categoryHelp.webhooks,
    },
  ];

  useEffect(() => {
    if (!loading) {
      setHasLoadedData(true);
    }
  }, [loading]);

  if (loading && !hasLoadedData) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 20 }}>
        <div>
          <Title level={3} style={{ marginBottom: 4 }}>
            {copy.title}
          </Title>
          <Text type="secondary">{copy.subtitle}</Text>
        </div>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={reload}>
            {copy.actions.refresh}
          </Button>
          <Button icon={<AuditOutlined />} onClick={() => navigate('/audit')}>
            {copy.actions.openAudit}
          </Button>
        </Space>
      </div>

      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} /> : null}

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 20 }}
        message={copy.integrationMap}
        description={copy.integrationDesc}
      />

      <Alert
        type="success"
        showIcon
        style={{ marginBottom: 20 }}
        message={copy.sourceOfTruth.title}
        description={copy.sourceOfTruth.description}
      />

      <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
        {integrationCards.map((card) => (
          <Col xs={24} md={12} xl={6} key={card.key}>
            <Card
              hoverable
              onClick={() => setActiveTab(card.tabKey)}
              style={{
                height: '100%',
                borderColor: activeTab === card.tabKey ? '#1677ff' : undefined,
              }}
            >
              <Space align="start">
                {card.icon}
                <div>
                  <Text strong>{card.title}</Text>
                  <Paragraph type="secondary" style={{ marginBottom: 8 }}>
                    {card.metric}
                  </Paragraph>
                  <Paragraph style={{ marginBottom: 8 }}>{card.help.summary}</Paragraph>
                  <Text type="secondary">{card.help.howTo}</Text>
                </div>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>

      <Card style={{ marginBottom: 16 }}>
        <Descriptions column={1} size="small">
          <Descriptions.Item label={copy.fields.apiBase}>{apiBase}</Descriptions.Item>
          <Descriptions.Item label={copy.fields.auth}>X-API-Key / Authorization: Bearer</Descriptions.Item>
          <Descriptions.Item label={copy.fields.swagger}>
            <Button size="small" onClick={() => window.open('/docs', '_blank')}>
              {copy.actions.openDocs}
            </Button>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'inbound',
            label: copy.tabs.inbound,
            children: (
              <InboundApiTab
                apiBase={apiBase}
                agents={agents}
                selectedAgentId={selectedAgentId}
                onSelectAgent={setSelectedAgentId}
                copy={copy}
              />
            ),
          },
          {
            key: 'outbound',
            label: copy.tabs.outbound,
            children: (
              <OutboundToolsTab
                agents={agents}
                apiBase={apiBase}
                tools={tools}
                copy={copy}
                onChanged={reload}
              />
            ),
          },
          {
            key: 'webhooks',
            label: copy.tabs.webhooks,
            children: <WorkflowWebhooksTab workflows={workflows} copy={copy} onChanged={reload} />,
          },
          {
            key: 'traces',
            label: copy.tabs.traces,
            children: <TraceDebugTab traces={recentTraces} copy={copy} />,
          },
        ]}
      />
    </div>
  );
}
