import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Input, Button, Card, Timeline, Tag, Empty, Descriptions, Space, Table, message } from 'antd';
import { SearchOutlined, ReloadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { auditApi } from '../api';

const EVENT_COLORS: Record<string, string> = {
  user_input: 'blue',
  intent: 'cyan',
  retrieval: 'green',
  llm_call: 'purple',
  tool_call: 'orange',
  workflow_step: 'geekblue',
  response: 'blue',
  escalation: 'red',
  error: 'red',
  risk_block: 'red',
  query_rewrite: 'cyan',
  function_calling_init: 'purple',
};

export default function AuditPage() {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const linkedTraceId = searchParams.get('trace_id');
  const [traceId, setTraceId] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [traces, setTraces] = useState<any[]>([]);
  const [recentTraces, setRecentTraces] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const loadRecent = async () => {
    try {
      const res = await auditApi.listTraces({ limit: 30 });
      setRecentTraces(res.data.items || []);
    } catch (e: any) {
      message.error(t('audit.loadRecentFailed'));
    }
  };

  const fetchTrace = async (value: string) => {
    if (!value) return;
    setLoading(true);
    try {
      const res = await auditApi.getTrace(value);
      setTraces(res.data);
      if (res.data.length === 0) {
        message.info(t('audit.noMatchingTrace'));
      }
    } catch (e: any) {
      message.error(`${t('audit.queryFailedPrefix')}: ` + (e.response?.data?.detail || e.message || t('common.unknown')));
      setTraces([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (linkedTraceId) {
      setTraceId(linkedTraceId);
      fetchTrace(linkedTraceId);
      return;
    }
    setTraces([]);
    loadRecent();
  }, [linkedTraceId]);

  const searchByTrace = async () => {
    await fetchTrace(traceId);
  };

  const searchBySession = async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const res = await auditApi.getSessionTraces(sessionId);
      setTraces(res.data);
      if (res.data.length === 0) {
        message.info(t('audit.noMatchingSession'));
      }
    } catch (e: any) {
      message.error(`${t('audit.queryFailedPrefix')}: ` + (e.response?.data?.detail || e.message || t('common.unknown')));
      setTraces([]);
    }
    setLoading(false);
  };

  const recentColumns = [
    { title: t('audit.eventType'), dataIndex: 'event_type', key: 'event_type', render: (v: string) => <Tag color={EVENT_COLORS[v] || 'default'}>{v}</Tag> },
    { title: t('audit.traceId'), dataIndex: 'trace_id', key: 'trace_id', ellipsis: true },
    { title: t('audit.agent'), dataIndex: 'agent_id', key: 'agent_id', ellipsis: true },
    { title: t('audit.latency'), dataIndex: 'latency_ms', key: 'latency_ms', render: (v: number) => v ? `${v.toFixed(1)}ms` : '-' },
    { title: t('audit.timestamp'), dataIndex: 'timestamp', key: 'timestamp', ellipsis: true },
    {
      title: t('common.actions'), key: 'actions', render: (_: any, r: any) => (
        <Button size="small" onClick={async () => {
          setTraceId(r.trace_id);
          await fetchTrace(r.trace_id);
        }}>
          {t('audit.viewChain')}
        </Button>
      ),
    },
  ];

  return (
    <div>
      <h2>{t('audit.title')}</h2>
      <Space style={{ marginBottom: 24 }}>
        <Input
          placeholder="Trace ID"
          value={traceId}
          onChange={e => setTraceId(e.target.value)}
          style={{ width: 320 }}
          onPressEnter={searchByTrace}
        />
        <Button icon={<SearchOutlined />} onClick={searchByTrace} loading={loading}>{t('audit.searchByTrace')}</Button>
        <Input
          placeholder="Session ID"
          value={sessionId}
          onChange={e => setSessionId(e.target.value)}
          style={{ width: 320 }}
          onPressEnter={searchBySession}
        />
        <Button icon={<SearchOutlined />} onClick={searchBySession} loading={loading}>{t('audit.searchBySession')}</Button>
      </Space>

      {traces.length > 0 ? (
        <Card title={t('audit.eventsCount', { count: traces.length })} style={{ marginBottom: 24 }}>
          <Timeline
            items={traces.map((tr: any) => ({
              color: EVENT_COLORS[tr.event_type] || 'gray',
              children: (
                <Card size="small" style={{ marginBottom: 8 }}>
                  <Descriptions size="small" column={2}>
                    <Descriptions.Item label={t('audit.eventType')}>
                      <Tag color={EVENT_COLORS[tr.event_type] || 'default'}>{tr.event_type}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label={t('audit.latency')}>
                      {tr.latency_ms ? `${tr.latency_ms.toFixed(1)}ms` : '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label={t('audit.timestamp')}>{tr.timestamp}</Descriptions.Item>
                    <Descriptions.Item label={t('audit.traceId')}>{tr.trace_id}</Descriptions.Item>
                  </Descriptions>
                  {tr.event_data && (
                    <pre style={{ fontSize: 12, maxHeight: 200, overflow: 'auto', background: '#f5f5f5', padding: 8, marginTop: 8 }}>
                      {JSON.stringify(tr.event_data, null, 2)}
                    </pre>
                  )}
                  {tr.retrieval_hits && (
                    <pre style={{ fontSize: 12, maxHeight: 200, overflow: 'auto', background: '#f0f5ff', padding: 8, marginTop: 8 }}>
                      {JSON.stringify(tr.retrieval_hits, null, 2)}
                    </pre>
                  )}
                  {tr.llm_meta && (
                    <pre style={{ fontSize: 12, background: '#f9f0ff', padding: 8, marginTop: 8 }}>
                      {JSON.stringify(tr.llm_meta, null, 2)}
                    </pre>
                  )}
                  {tr.tool_meta && (
                    <pre style={{ fontSize: 12, background: '#fff7e6', padding: 8, marginTop: 8 }}>
                      {JSON.stringify(tr.tool_meta, null, 2)}
                    </pre>
                  )}
                </Card>
              ),
            }))}
          />
        </Card>
      ) : (
        <>
          <Card
            title={t('audit.recentEvents')}
            extra={<Button icon={<ReloadOutlined />} size="small" onClick={loadRecent}>{t('common.refresh')}</Button>}
          >
            {recentTraces.length > 0 ? (
              <Table
                dataSource={recentTraces}
                columns={recentColumns}
                rowKey="id"
                size="small"
                pagination={{ pageSize: 10 }}
              />
            ) : (
              <Empty description={t('audit.emptyDescription')} />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
