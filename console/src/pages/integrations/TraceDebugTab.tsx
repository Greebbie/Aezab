import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AuditOutlined } from '@ant-design/icons';
import { Button, Card, Select, Space, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { AuditTraceSummary } from '../../types';
import type { IntegrationCopy } from './copy';

const OPEN_AUDIT_LABEL = 'Open Audit';

interface Props {
  traces: AuditTraceSummary[];
  copy: IntegrationCopy;
}

export default function TraceDebugTab({ traces, copy }: Props) {
  const navigate = useNavigate();
  const [eventType, setEventType] = useState<string>();

  const eventOptions = useMemo(
    () =>
      Array.from(new Set(traces.map((trace) => trace.event_type).filter(Boolean))).map((value) => ({
        value,
        label: value,
      })),
    [traces],
  );

  const recentTraces = useMemo(
    () => (eventType ? traces.filter((trace) => trace.event_type === eventType) : traces),
    [eventType, traces],
  );

  const columns: ColumnsType<AuditTraceSummary> = [
    {
      title: copy.fields.eventType,
      dataIndex: 'event_type',
      key: 'event_type',
      render: (value: string) => <Tag>{value}</Tag>,
    },
    { title: 'Trace ID', dataIndex: 'trace_id', key: 'trace_id', ellipsis: true },
    { title: 'Agent', dataIndex: 'agent_id', key: 'agent_id', ellipsis: true },
    {
      title: copy.fields.latency,
      dataIndex: 'latency_ms',
      key: 'latency_ms',
      width: 110,
      render: (value: number | null) => (value ? `${value.toFixed(0)}ms` : '-'),
    },
    {
      title: '',
      key: 'actions',
      width: 130,
      render: (_, record) => (
        <Button size="small" onClick={() => navigate(`/audit?trace_id=${encodeURIComponent(record.trace_id)}`)}>
          {copy.actions.openAudit || OPEN_AUDIT_LABEL}
        </Button>
      ),
    },
  ];

  return (
    <Card
      title={copy.tabs.traces}
      extra={
        <Button icon={<AuditOutlined />} onClick={() => navigate('/audit')}>
          {copy.actions.openAudit || OPEN_AUDIT_LABEL}
        </Button>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Select
          allowClear
          style={{ width: 240, maxWidth: '100%' }}
          placeholder={copy.fields.eventType}
          value={eventType}
          onChange={setEventType}
          options={eventOptions}
        />
        <Table
          size="small"
          dataSource={recentTraces}
          columns={columns}
          rowKey="id"
          pagination={{ pageSize: 10 }}
          locale={{ emptyText: copy.empty.noTraces }}
        />
      </Space>
    </Card>
  );
}
