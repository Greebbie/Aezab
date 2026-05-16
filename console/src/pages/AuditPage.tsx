import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Input, Button, Card, Timeline, Tag, Empty, Descriptions, Space, Table, message } from 'antd';
import { SearchOutlined, ReloadOutlined } from '@ant-design/icons';
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
      message.error('加载最近记录失败');
    }
  };

  const fetchTrace = async (value: string) => {
    if (!value) return;
    setLoading(true);
    try {
      const res = await auditApi.getTrace(value);
      setTraces(res.data);
      if (res.data.length === 0) {
        message.info('未找到匹配的 Trace');
      }
    } catch (e: any) {
      message.error('查询失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
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
        message.info('未找到匹配的会话');
      }
    } catch (e: any) {
      message.error('查询失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
      setTraces([]);
    }
    setLoading(false);
  };

  const recentColumns = [
    { title: '事件类型', dataIndex: 'event_type', key: 'event_type', render: (v: string) => <Tag color={EVENT_COLORS[v] || 'default'}>{v}</Tag> },
    { title: 'Trace ID', dataIndex: 'trace_id', key: 'trace_id', ellipsis: true },
    { title: 'Agent', dataIndex: 'agent_id', key: 'agent_id', ellipsis: true },
    { title: '延迟', dataIndex: 'latency_ms', key: 'latency_ms', render: (v: number) => v ? `${v.toFixed(1)}ms` : '-' },
    { title: '时间', dataIndex: 'timestamp', key: 'timestamp', ellipsis: true },
    {
      title: '操作', key: 'actions', render: (_: any, r: any) => (
        <Button size="small" onClick={async () => {
          setTraceId(r.trace_id);
          await fetchTrace(r.trace_id);
        }}>
          查看链路
        </Button>
      ),
    },
  ];

  return (
    <div>
      <h2>审计回放</h2>
      <Space style={{ marginBottom: 24 }}>
        <Input
          placeholder="Trace ID"
          value={traceId}
          onChange={e => setTraceId(e.target.value)}
          style={{ width: 320 }}
          onPressEnter={searchByTrace}
        />
        <Button icon={<SearchOutlined />} onClick={searchByTrace} loading={loading}>按 Trace 查询</Button>
        <Input
          placeholder="Session ID"
          value={sessionId}
          onChange={e => setSessionId(e.target.value)}
          style={{ width: 320 }}
          onPressEnter={searchBySession}
        />
        <Button icon={<SearchOutlined />} onClick={searchBySession} loading={loading}>按会话查询</Button>
      </Space>

      {traces.length > 0 ? (
        <Card title={`共 ${traces.length} 条事件`} style={{ marginBottom: 24 }}>
          <Timeline
            items={traces.map((t: any) => ({
              color: EVENT_COLORS[t.event_type] || 'gray',
              children: (
                <Card size="small" style={{ marginBottom: 8 }}>
                  <Descriptions size="small" column={2}>
                    <Descriptions.Item label="事件类型">
                      <Tag color={EVENT_COLORS[t.event_type] || 'default'}>{t.event_type}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="延迟">
                      {t.latency_ms ? `${t.latency_ms.toFixed(1)}ms` : '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="时间">{t.timestamp}</Descriptions.Item>
                    <Descriptions.Item label="Trace ID">{t.trace_id}</Descriptions.Item>
                  </Descriptions>
                  {t.event_data && (
                    <pre style={{ fontSize: 12, maxHeight: 200, overflow: 'auto', background: '#f5f5f5', padding: 8, marginTop: 8 }}>
                      {JSON.stringify(t.event_data, null, 2)}
                    </pre>
                  )}
                  {t.retrieval_hits && (
                    <pre style={{ fontSize: 12, maxHeight: 200, overflow: 'auto', background: '#f0f5ff', padding: 8, marginTop: 8 }}>
                      {JSON.stringify(t.retrieval_hits, null, 2)}
                    </pre>
                  )}
                  {t.llm_meta && (
                    <pre style={{ fontSize: 12, background: '#f9f0ff', padding: 8, marginTop: 8 }}>
                      {JSON.stringify(t.llm_meta, null, 2)}
                    </pre>
                  )}
                  {t.tool_meta && (
                    <pre style={{ fontSize: 12, background: '#fff7e6', padding: 8, marginTop: 8 }}>
                      {JSON.stringify(t.tool_meta, null, 2)}
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
            title="最近事件"
            extra={<Button icon={<ReloadOutlined />} size="small" onClick={loadRecent}>刷新</Button>}
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
              <Empty description="暂无审计记录，发送消息后可在此查看完整链路" />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
