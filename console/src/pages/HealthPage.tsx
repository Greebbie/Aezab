import { useEffect, useState, useCallback } from 'react';
import { Card, Row, Col, Tag, Typography, Spin, Button, Descriptions, Space } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  ReloadOutlined,
  DatabaseOutlined,
  CloudServerOutlined,
  ThunderboltOutlined,
  RobotOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { healthApi } from '../api';
import { friendlyError } from '../utils/friendlyError';

const { Title, Text } = Typography;

interface ComponentHealth {
  status: string;
  error?: string;
  count?: number;
  total?: number;
  open?: number;
  model?: string;
  base_url?: string;
  message?: string;
  message_key?: string;
}

interface HealthStatus {
  status: string;
  version: string;
  components: Record<string, ComponentHealth>;
}

const STATUS_CONFIG: Record<string, { color: string; icon: React.ReactNode }> = {
  healthy: { color: 'success', icon: <CheckCircleOutlined /> },
  unhealthy: { color: 'error', icon: <CloseCircleOutlined /> },
  degraded: { color: 'warning', icon: <ExclamationCircleOutlined /> },
  not_initialized: { color: 'default', icon: <ExclamationCircleOutlined /> },
  // LLM-specific statuses returned by server/engine/llm_health.py
  auth_error: { color: 'error', icon: <CloseCircleOutlined /> },
  rate_limited: { color: 'warning', icon: <ExclamationCircleOutlined /> },
  unreachable: { color: 'error', icon: <CloseCircleOutlined /> },
  not_configured: { color: 'default', icon: <ExclamationCircleOutlined /> },
  error: { color: 'error', icon: <CloseCircleOutlined /> },
};

const COMPONENT_ICONS: Record<string, React.ReactNode> = {
  database: <DatabaseOutlined />,
  vector_store: <CloudServerOutlined />,
  circuit_breakers: <ThunderboltOutlined />,
  llm: <RobotOutlined />,
};

// LLM statuses where sending the user to the LLM Configs page to re-test
// the connection is the actual fix.
const LLM_ACTIONABLE_STATUSES = new Set(['auth_error', 'rate_limited', 'unreachable', 'not_configured', 'error']);

export default function HealthPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [rechecking, setRechecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadHealth = useCallback(async (force = false) => {
    if (force) setRechecking(true);
    else setLoading(true);
    setError(null);
    try {
      const res = await healthApi.check({ checkLlm: true, force });
      setHealth(res.data);
    } catch (e: unknown) {
      setError(friendlyError(e, t));
    } finally {
      setLoading(false);
      setRechecking(false);
    }
  }, [t]);

  useEffect(() => {
    loadHealth();
    const timer = setInterval(() => loadHealth(), 30000);
    return () => clearInterval(timer);
  }, [loadHealth]);

  if (loading && !health) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={3}>{t('health.title')}</Title>
        <Button icon={<ReloadOutlined />} onClick={() => loadHealth()} loading={loading}>
          {t('common.refresh')}
        </Button>
      </div>

      {error && (
        <Card style={{ marginBottom: 16 }}>
          <Tag color="error">Unreachable</Tag> {error}
        </Card>
      )}

      {health && (
        <>
          <Card style={{ marginBottom: 16 }}>
            <Descriptions column={3}>
              <Descriptions.Item label="Overall Status">
                <Tag
                  color={health.status === 'ok' ? 'success' : 'warning'}
                  icon={health.status === 'ok' ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}
                >
                  {health.status.toUpperCase()}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Version">{health.version}</Descriptions.Item>
              <Descriptions.Item label="Components">{Object.keys(health.components).length}</Descriptions.Item>
            </Descriptions>
          </Card>

          <Row gutter={[16, 16]}>
            {Object.entries(health.components).map(([name, comp]) => {
              const cfg = STATUS_CONFIG[comp.status] || STATUS_CONFIG.healthy;
              const isLlm = name === 'llm';
              return (
                <Col xs={24} sm={12} md={8} key={name}>
                  <Card
                    title={
                      <span>
                        {COMPONENT_ICONS[name] || <CloudServerOutlined />}{' '}
                        {isLlm ? t('health.llmComponent') : name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                      </span>
                    }
                    extra={<Tag color={cfg.color} icon={cfg.icon}>{comp.status}</Tag>}
                  >
                    {isLlm ? (
                      <Space direction="vertical" size="small" style={{ width: '100%' }}>
                        {comp.message && (
                          <Text>
                            {comp.message_key
                              ? t(`health.llm.${comp.message_key}`, { defaultValue: comp.message })
                              : comp.message}
                          </Text>
                        )}
                        {comp.model && <Text type="secondary" style={{ fontSize: 12 }}>Model: {comp.model}</Text>}
                        {comp.base_url && <Text type="secondary" style={{ fontSize: 12 }}>Base URL: {comp.base_url}</Text>}
                        <Space wrap>
                          <Button
                            size="small"
                            icon={<ReloadOutlined />}
                            loading={rechecking}
                            onClick={() => loadHealth(true)}
                          >
                            {t('health.recheck')}
                          </Button>
                          {LLM_ACTIONABLE_STATUSES.has(comp.status) && (
                            <Button
                              size="small"
                              type="primary"
                              icon={<SettingOutlined />}
                              onClick={() => navigate('/llm-configs')}
                            >
                              {t('health.goToLlmConfigs')}
                            </Button>
                          )}
                        </Space>
                      </Space>
                    ) : (
                      <>
                        {comp.error && <Text type="danger" style={{ fontSize: 12 }}>{comp.error}</Text>}
                        {comp.count != null && <div><Text type="secondary">Vectors: {comp.count}</Text></div>}
                        {comp.total != null && (
                          <div><Text type="secondary">Circuits: {comp.total} (open: {comp.open || 0})</Text></div>
                        )}
                        {!comp.error && comp.count == null && comp.total == null && (
                          <Text type="secondary">Running normally</Text>
                        )}
                      </>
                    )}
                  </Card>
                </Col>
              );
            })}
          </Row>
        </>
      )}
    </div>
  );
}
