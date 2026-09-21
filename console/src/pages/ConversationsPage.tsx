import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Alert, Button, Card, Col, Empty, Row, Select, Space, Spin, Table, Tag, Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { MessageOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons';
import { agentApi, sessionsApi } from '../api';
import type { Agent, ConversationMessage, ConversationSessionSummary } from '../types';
import { friendlyError } from '../utils/friendlyError';
import { PageHeader } from '../components/shared';

const { Text } = Typography;

const PAGE_SIZE = 20;
const MESSAGE_PAGE_SIZE = 50;

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
  const [selectedSession, setSelectedSession] = useState<ConversationSessionSummary | null>(null);
  const [messagePage, setMessagePage] = useState({ offset: 0, total: 0 });
  const listRequest = useRef(0);
  const messageRequest = useRef(0);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const scrollToLatest = useRef(true);

  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = scrollToLatest.current ? transcriptRef.current.scrollHeight : 0;
    }
  }, [messages, messagesLoading]);

  useEffect(() => () => {
    listRequest.current += 1;
    messageRequest.current += 1;
  }, []);

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
    const requestId = ++listRequest.current;
    setListLoading(true);
    setListError(null);
    try {
      const res = await sessionsApi.list({
        agent_id: agentFilter,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      if (requestId !== listRequest.current) return;
      setSessions(res.data.items);
      setTotal(res.data.total);
    } catch (e: unknown) {
      if (requestId !== listRequest.current) return;
      setListError(friendlyError(e, t));
      setSessions([]);
      setTotal(0);
    } finally {
      if (requestId === listRequest.current) setListLoading(false);
    }
  }, [agentFilter, page, t]);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const loadMessages = useCallback(async (sessionId: string, offset?: number) => {
    const requestId = ++messageRequest.current;
    setSelectedId(sessionId);
    setMessagesLoading(true);
    setMessagesError(null);
    try {
      const [res, detail] = await Promise.all([
        sessionsApi.messages(sessionId, {
          limit: MESSAGE_PAGE_SIZE, offset, latest: offset === undefined,
        }),
        sessionsApi.get(sessionId),
      ]);
      if (requestId !== messageRequest.current) return;
      scrollToLatest.current = offset === undefined;
      setMessages(res.data.items);
      setMessagePage({ offset: res.data.offset, total: res.data.total });
      setSelectedSession(detail.data);
    } catch (e: unknown) {
      if (requestId !== messageRequest.current) return;
      setMessagesError(friendlyError(e, t));
      setMessages([]);
      setSelectedSession(null);
    } finally {
      if (requestId === messageRequest.current) setMessagesLoading(false);
    }
  }, [t]);

  const columns: ColumnsType<ConversationSessionSummary> = [
    {
      title: t('conversations.summary'),
      dataIndex: 'title',
      key: 'title',
      render: (title: string | null, record) => (
        <Space direction="vertical" size={2} style={{ maxWidth: '100%', overflowWrap: 'anywhere' }}>
          <Text>{title || t('conversations.noSummary')}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {agentName(record.agent_id)} · {record.message_count} {t('conversations.messageCount')}
          </Text>
          <Text type="secondary" style={{ fontSize: 11 }}>{formatDate(record.updated_at)}</Text>
        </Space>
      ),
    },
    {
      title: t('conversations.status'),
      dataIndex: 'status',
      key: 'status',
      width: 150,
      render: (status: string) => <Tag style={{ whiteSpace: 'normal', marginInlineEnd: 0 }}>{t(`conversations.statuses.${status}`, { defaultValue: status })}</Tag>,
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
              <Space wrap>
              <Button onClick={loadSessions} loading={listLoading}>{t('conversations.refresh')}</Button>
              <Select
                allowClear
                placeholder={t('conversations.filterByAgent')}
                style={{ width: 180 }}
                value={agentFilter}
                onChange={(value) => {
                  setAgentFilter(value); setPage(1); setSelectedId(null); setSelectedSession(null);
                  messageRequest.current += 1;
                }}
                options={agents.map((a) => ({ value: a.id, label: a.name }))}
              />
              </Space>
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
          <Card className="aezab-card" size="small" title={t('conversations.transcript')}
            extra={selectedId && selectedSession && !messagesLoading ? (
              <Link to={`/playground?session=${encodeURIComponent(selectedId)}`}>
                <Button type="primary" size="small">{t('conversations.continue')}</Button>
              </Link>
            ) : undefined}>
            {selectedSession && !messagesLoading && (
              <Space direction="vertical" size={4} style={{ marginBottom: 12 }}>
                <Text copyable>{selectedSession.id}</Text>
                <Space wrap>
                  <Tag>{t(`conversations.statuses.${selectedSession.status}`, { defaultValue: selectedSession.status })}</Tag>
                  <Text type="secondary">{t('conversations.user')}: {selectedSession.user_id}</Text>
                  {selectedSession.workflow_version != null && <Text type="secondary">v{selectedSession.workflow_version}</Text>}
                </Space>
              </Space>
            )}
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
              <>
              <Space wrap style={{ marginBottom: 12 }}>
                <Button size="small" disabled={messagePage.offset === 0}
                  onClick={() => loadMessages(selectedId, Math.max(0, messagePage.offset - MESSAGE_PAGE_SIZE))}>
                  {t('conversations.older')}
                </Button>
                <Button size="small" disabled={messagePage.offset + messages.length >= messagePage.total}
                  onClick={() => loadMessages(selectedId, messagePage.offset + MESSAGE_PAGE_SIZE)}>
                  {t('conversations.newer')}
                </Button>
                <Button size="small" onClick={() => loadMessages(selectedId)}>{t('conversations.latest')}</Button>
                <Text type="secondary">{t('conversations.messageRange', {
                  start: messagePage.offset + 1, end: messagePage.offset + messages.length, total: messagePage.total,
                })}</Text>
              </Space>
              <div ref={transcriptRef} style={{ maxHeight: 560, overflowY: 'auto', padding: '4px 4px' }}>
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
                            {msg.trace_id && <Link to={`/audit?trace_id=${encodeURIComponent(msg.trace_id)}`}>{t('conversations.trace')}</Link>}
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
              </>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
