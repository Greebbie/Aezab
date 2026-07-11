import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert, Card, Col, Empty, Row, Select, Space, Spin, Table, Tag, Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { MessageOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons';
import { agentApi, sessionsApi } from '../api';
import type { Agent, ConversationMessage, ConversationSessionSummary } from '../types';
import { friendlyError } from '../utils/friendlyError';
import { PageHeader } from '../components/shared';

const { Text } = Typography;

const PAGE_SIZE = 20;

function formatDate(value: string | null): string {
  if (!value) return '-';
  return new Date(value).toLocaleString();
}

export default function ConversationsPage() {
  const { t } = useTranslation();

  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentFilter, setAgentFilter] = useState<string | undefined>(undefined);

  const [sessions, setSessions] = useState<ConversationSessionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);

  const agentName = useCallback(
    (agentId: string) => agents.find((a) => a.id === agentId)?.name || agentId,
    [agents],
  );

  useEffect(() => {
    agentApi.list().then((res) => setAgents(res.data)).catch(() => {
      // Non-fatal — the filter dropdown just stays empty and the table
      // falls back to raw agent ids.
    });
  }, []);

  const loadSessions = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    try {
      const res = await sessionsApi.list({
        agent_id: agentFilter,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      setSessions(res.data.items);
      setTotal(res.data.total);
    } catch (e: unknown) {
      setListError(friendlyError(e, t));
      setSessions([]);
      setTotal(0);
    } finally {
      setListLoading(false);
    }
  }, [agentFilter, page, t]);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const loadMessages = useCallback(async (sessionId: string) => {
    setSelectedId(sessionId);
    setMessagesLoading(true);
    setMessagesError(null);
    try {
      const res = await sessionsApi.messages(sessionId, { limit: 500 });
      setMessages(res.data.items);
    } catch (e: unknown) {
      setMessagesError(friendlyError(e, t));
      setMessages([]);
    } finally {
      setMessagesLoading(false);
    }
  }, [t]);

  const columns: ColumnsType<ConversationSessionSummary> = [
    {
      title: t('conversations.agent'),
      dataIndex: 'agent_id',
      key: 'agent_id',
      render: (agentId: string) => <Tag color="blue">{agentName(agentId)}</Tag>,
    },
    {
      title: t('conversations.summary'),
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (title: string | null) => title || <Text type="secondary">{t('conversations.noSummary')}</Text>,
    },
    {
      title: t('conversations.messageCount'),
      dataIndex: 'message_count',
      key: 'message_count',
      width: 90,
      align: 'center',
    },
    {
      title: t('conversations.startedAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: formatDate,
    },
    {
      title: t('conversations.lastActive'),
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 170,
      render: formatDate,
    },
  ];

  return (
    <div>
      <PageHeader
        eyebrow={t('conversations.eyebrow')}
        title={t('conversations.title')}
        description={t('conversations.description')}
      />

      {listError && <Alert message={listError} type="error" style={{ marginBottom: 16 }} closable />}

      <Row gutter={16}>
        <Col xs={24} lg={11}>
          <Card
            className="aezab-card"
            size="small"
            title={t('conversations.sessionList')}
            extra={(
              <Select
                allowClear
                placeholder={t('conversations.filterByAgent')}
                style={{ width: 180 }}
                value={agentFilter}
                onChange={(value) => { setAgentFilter(value); setPage(1); }}
                options={agents.map((a) => ({ value: a.id, label: a.name }))}
              />
            )}
          >
            <Table
              size="small"
              rowKey="id"
              dataSource={sessions}
              columns={columns}
              loading={listLoading}
              locale={{ emptyText: <Empty description={t('conversations.noSessions')} /> }}
              pagination={{
                current: page,
                pageSize: PAGE_SIZE,
                total,
                onChange: setPage,
                showSizeChanger: false,
              }}
              onRow={(record) => ({
                onClick: () => loadMessages(record.id),
                style: {
                  cursor: 'pointer',
                  background: record.id === selectedId ? '#e6f4ff' : undefined,
                },
              })}
            />
          </Card>
        </Col>

        <Col xs={24} lg={13}>
          <Card className="aezab-card" size="small" title={t('conversations.transcript')}>
            {!selectedId && (
              <Empty
                image={<MessageOutlined style={{ fontSize: 40, color: '#bbb' }} />}
                description={t('conversations.selectSessionHint')}
              />
            )}

            {selectedId && messagesError && (
              <Alert message={messagesError} type="error" style={{ marginBottom: 12 }} closable />
            )}

            {selectedId && messagesLoading && (
              <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}>
                <Spin />
              </div>
            )}

            {selectedId && !messagesLoading && !messagesError && messages.length === 0 && (
              <Empty description={t('conversations.noMessages')} />
            )}

            {selectedId && !messagesLoading && messages.length > 0 && (
              <div style={{ maxHeight: 560, overflowY: 'auto', padding: '4px 4px' }}>
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  {messages.map((msg) => {
                    const isUser = msg.role === 'user';
                    const text = msg.short_answer || msg.content;
                    return (
                      <div
                        key={msg.id}
                        style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start' }}
                      >
                        <div style={{ maxWidth: '80%', display: 'flex', flexDirection: 'column', alignItems: isUser ? 'flex-end' : 'flex-start' }}>
                          <Space size={4} style={{ marginBottom: 4 }}>
                            {isUser ? (
                              <Tag icon={<UserOutlined />} color="blue">{t('conversations.roleUser')}</Tag>
                            ) : (
                              <Tag icon={<RobotOutlined />} color="green">{t('conversations.roleAssistant')}</Tag>
                            )}
                            <Text type="secondary" style={{ fontSize: 12 }}>{formatDate(msg.created_at)}</Text>
                          </Space>
                          <div
                            style={{
                              padding: '10px 14px',
                              borderRadius: isUser ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
                              background: isUser ? '#1677ff' : '#f5f5f5',
                              color: isUser ? '#fff' : '#333',
                              whiteSpace: 'pre-wrap',
                              wordBreak: 'break-word',
                              lineHeight: 1.6,
                            }}
                          >
                            {text || <Text type="secondary" italic>{t('conversations.emptyMessage')}</Text>}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </Space>
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
