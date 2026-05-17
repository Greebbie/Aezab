import { useEffect, useState, useCallback } from 'react';
import { Card, Col, Row, Statistic, Spin, Button, Alert, Tag, Table, Typography, Space } from 'antd';
import {
  MessageOutlined,
  SearchOutlined,
  ApiOutlined,
  UserSwitchOutlined,
  RobotOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { auditApi, agentApi, performanceApi } from '../api';
import type { AuditMetrics } from '../types';
import { HelpLabel, PageHeader } from '../components/shared';

interface CircuitStatus {
  service: string;
  state: 'closed' | 'open' | 'half_open';
  failures: number;
  successes: number;
}

export default function DashboardPage() {
  const { t } = useTranslation();
  const [metrics, setMetrics] = useState<AuditMetrics | null>(null);
  const [agentCount, setAgentCount] = useState(0);
  const [circuits, setCircuits] = useState<CircuitStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [metricsRes, agentsRes, circuitRes] = await Promise.all([
        auditApi.getMetrics('default', 24),
        agentApi.list(),
        performanceApi.getCircuitBreakerStatus().catch(() => ({ data: { circuits: [] } })),
      ]);
      setMetrics(metricsRes.data);
      setAgentCount(Array.isArray(agentsRes.data) ? agentsRes.data.length : 0);
      setCircuits(circuitRes.data?.circuits || []);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Unknown error';
      setError(`${t('common.error')}: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    loadData();
    const timer = setInterval(loadData, 60000);
    return () => clearInterval(timer);
  }, [loadData]);

  if (loading && !metrics) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }

  const m = metrics || {} as Record<string, number>;

  const openCircuitCount = circuits.filter((c) => c.state === 'open').length;
  const readinessItems = [
    {
      label: t('dashboard.agentReadiness'),
      help: t('dashboard.help.agentReadiness'),
      ok: agentCount > 0,
      value: agentCount > 0 ? t('dashboard.ready') : t('dashboard.needsSetup'),
    },
    {
      label: t('dashboard.invokeSignal'),
      help: t('dashboard.help.invokeSignal'),
      ok: ((m as any).total_invocations || 0) > 0,
      value: `${(m as any).total_invocations || 0}`,
    },
    {
      label: t('dashboard.retrievalSignal'),
      help: t('dashboard.help.retrievalSignal'),
      ok: ((m as any).retrieval_count || 0) > 0,
      value: `${(m as any).retrieval_count || 0}`,
    },
    {
      label: t('dashboard.circuitHealth'),
      help: t('dashboard.help.circuitHealth'),
      ok: openCircuitCount === 0,
      value: openCircuitCount === 0 ? t('dashboard.healthy') : `${openCircuitCount} ${t('dashboard.openCircuits')}`,
    },
  ];

  const circuitColumns = [
    { title: t('dashboard.service'), dataIndex: 'service', key: 'service' },
    {
      title: t('dashboard.state'),
      dataIndex: 'state',
      key: 'state',
      render: (state: string) => {
        const config: Record<string, { color: string; icon: React.ReactNode }> = {
          closed: { color: 'success', icon: <CheckCircleOutlined /> },
          open: { color: 'error', icon: <CloseCircleOutlined /> },
          half_open: { color: 'warning', icon: <ExclamationCircleOutlined /> },
        };
        const c = config[state] || config.closed;
        return <Tag color={c.color} icon={c.icon}>{state.toUpperCase()}</Tag>;
      },
    },
    { title: t('dashboard.failures'), dataIndex: 'failures', key: 'failures' },
    { title: t('dashboard.successes'), dataIndex: 'successes', key: 'successes' },
  ];

  return (
    <div>
      <PageHeader
        eyebrow={t('dashboard.eyebrow')}
        title={t('dashboard.title')}
        description={t('dashboard.description')}
        status={t('dashboard.last24h')}
        actions={(
          <Button icon={<ReloadOutlined />} onClick={loadData} loading={loading}>
            {t('common.refresh')}
          </Button>
        )}
      />

      {error && <Alert message={error} type="error" style={{ marginBottom: 16 }} closable />}

      <Card className="aezab-card" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Typography.Text strong>{t('dashboard.readiness')}</Typography.Text>
          <Row gutter={[12, 12]}>
            {readinessItems.map((item) => (
              <Col xs={24} md={12} xl={6} key={item.label}>
                <div className="aezab-stat-card">
                  <div className="aezab-stat-label">
                    <HelpLabel label={item.label} help={item.help} />
                  </div>
                  <Space>
                    <Tag color={item.ok ? 'success' : 'warning'}>
                      {item.ok ? t('dashboard.ok') : t('dashboard.review')}
                    </Tag>
                    <Typography.Text strong>{item.value}</Typography.Text>
                  </Space>
                </div>
              </Col>
            ))}
          </Row>
        </Space>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={12} sm={8} md={4}>
          <Card size="small" className="aezab-card">
            <Statistic title={t('dashboard.activeAgents')} value={agentCount} prefix={<RobotOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={5}>
          <Card size="small" className="aezab-card">
            <Statistic
              title={<HelpLabel label={t('dashboard.totalRequests')} help={t('dashboard.help.totalRequests')} />}
              value={(m as any).total_invocations || 0}
              prefix={<MessageOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={5}>
          <Card size="small" className="aezab-card">
            <Statistic
              title={<HelpLabel label={t('dashboard.retrieval')} help={t('dashboard.help.retrieval')} />}
              value={(m as any).retrieval_count || 0}
              prefix={<SearchOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={5}>
          <Card size="small" className="aezab-card">
            <Statistic
              title={<HelpLabel label={t('dashboard.toolCalls')} help={t('dashboard.help.toolCalls')} />}
              value={(m as any).tool_call_count || 0}
              prefix={<ApiOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={5}>
          <Card size="small" className="aezab-card">
            <Statistic title={t('dashboard.escalationRate')} value={(m as any).escalation_rate || 0} suffix="%" prefix={<UserSwitchOutlined />} />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={12} md={6}>
          <Card size="small" className="aezab-card">
            <Statistic
              title={<HelpLabel label={t('dashboard.avgLatency')} help={t('dashboard.help.avgLatency')} />}
              value={(m as any).avg_retrieval_latency_ms || 0}
              suffix="ms"
              precision={1}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small" className="aezab-card">
            <Statistic
              title={<HelpLabel label={t('dashboard.avgLLMLatency')} help={t('dashboard.help.avgLLMLatency')} />}
              value={(m as any).avg_llm_latency_ms || 0}
              suffix="ms"
              precision={1}
            />
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card
            title={(
              <>
                <ThunderboltOutlined />{' '}
                <HelpLabel label={t('dashboard.circuitBreaker')} help={t('dashboard.help.circuitBreaker')} />
              </>
            )}
            size="small"
            className="aezab-card"
          >
            {circuits.length === 0 ? (
              <Typography.Text type="secondary">{t('dashboard.noCircuits')}</Typography.Text>
            ) : (
              <Table
                dataSource={circuits}
                columns={circuitColumns}
                size="small"
                pagination={false}
                rowKey="service"
              />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
