import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert, Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { subscriptionsApi } from '../../api';
import type { EventSubscription, EventSubscriptionCreate, EventSubscriptionUpdate } from '../../types';

const { Text, Paragraph } = Typography;

const KNOWN_EVENT_TYPES = ['workflow.completed', 'workflow.submit_failed', 'workflow.escalated', '*'];

interface SubscriptionFormValues {
  name: string;
  url: string;
  secret?: string;
  events: string[];
  enabled: boolean;
}

function isFormValidationError(error: unknown): error is { errorFields: unknown } {
  return typeof error === 'object' && error !== null && 'errorFields' in error;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export default function EventSubscriptionsTab() {
  const { t } = useTranslation();
  const [subscriptions, setSubscriptions] = useState<EventSubscription[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<EventSubscription | null>(null);
  const [form] = Form.useForm<SubscriptionFormValues>();

  const load = async () => {
    setLoading(true);
    try {
      const res = await subscriptionsApi.list();
      setSubscriptions(res.data);
    } catch (error: unknown) {
      message.error(errorMessage(error, t('eventSubscriptions.loadFailed')));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // Load once on mount; `load` is intentionally not memoized.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const closeModal = () => {
    setOpen(false);
    setEditing(null);
    form.resetFields();
  };

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ events: [], enabled: true });
    setOpen(true);
  };

  const openEdit = (sub: EventSubscription) => {
    setEditing(sub);
    form.setFieldsValue({
      name: sub.name,
      url: sub.url,
      secret: undefined,
      events: sub.events,
      enabled: sub.enabled,
    });
    setOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      if (editing) {
        const payload: EventSubscriptionUpdate = {
          name: values.name,
          url: values.url,
          events: values.events,
          enabled: values.enabled,
        };
        if (values.secret) {
          payload.secret = values.secret;
        }
        await subscriptionsApi.update(editing.id, payload);
        message.success(t('eventSubscriptions.updateSuccess'));
      } else {
        const payload: EventSubscriptionCreate = {
          name: values.name,
          url: values.url,
          secret: values.secret || '',
          events: values.events,
          enabled: values.enabled ?? true,
        };
        await subscriptionsApi.create(payload);
        message.success(t('eventSubscriptions.createSuccess'));
      }
      closeModal();
      await load();
    } catch (error: unknown) {
      if (isFormValidationError(error)) return;
      message.error(errorMessage(error, t('eventSubscriptions.saveFailed')));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await subscriptionsApi.delete(id);
      message.success(t('eventSubscriptions.deleteSuccess'));
      await load();
    } catch (error: unknown) {
      message.error(errorMessage(error, t('eventSubscriptions.deleteFailed')));
    }
  };

  const handleToggleEnabled = async (sub: EventSubscription, enabled: boolean) => {
    try {
      await subscriptionsApi.update(sub.id, { enabled });
      setSubscriptions((prev) => prev.map((item) => (item.id === sub.id ? { ...item, enabled } : item)));
    } catch (error: unknown) {
      message.error(errorMessage(error, t('eventSubscriptions.toggleFailed')));
    }
  };

  const columns: ColumnsType<EventSubscription> = [
    { title: t('eventSubscriptions.name'), dataIndex: 'name', key: 'name' },
    { title: t('eventSubscriptions.url'), dataIndex: 'url', key: 'url', ellipsis: true },
    {
      title: t('eventSubscriptions.events'),
      dataIndex: 'events',
      key: 'events',
      render: (events: string[]) => (
        <Space size={4} wrap>
          {events.map((event) => (
            <Tag key={event} color={event === '*' ? 'gold' : 'blue'}>
              {event === '*' ? t('eventSubscriptions.allEvents') : event}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: t('eventSubscriptions.enabled'),
      dataIndex: 'enabled',
      key: 'enabled',
      width: 100,
      render: (enabled: boolean, record) => (
        <Switch checked={enabled} onChange={(checked) => handleToggleEnabled(record, checked)} />
      ),
    },
    {
      title: t('eventSubscriptions.createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (value: string | null) => (value ? new Date(value).toLocaleString() : '-'),
    },
    {
      title: t('eventSubscriptions.actions'),
      key: 'actions',
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => openEdit(record)}>{t('eventSubscriptions.edit')}</Button>
          <Popconfirm title={t('eventSubscriptions.deleteConfirm')} onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger>{t('eventSubscriptions.delete')}</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={t('eventSubscriptions.title')}
        description={(
          <Paragraph style={{ marginBottom: 0 }}>
            <Text>{t('eventSubscriptions.verificationHint')}</Text>
            {' '}
            <Text type="secondary">({t('eventSubscriptions.docsLink')}: docs/integration.md)</Text>
          </Paragraph>
        )}
      />

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          {t('eventSubscriptions.create')}
        </Button>
      </div>

      <Table
        size="small"
        rowKey="id"
        dataSource={subscriptions}
        columns={columns}
        loading={loading}
        pagination={{ pageSize: 8 }}
        locale={{ emptyText: t('eventSubscriptions.noSubscriptions') }}
      />

      <Modal
        title={editing ? t('eventSubscriptions.edit') : t('eventSubscriptions.create')}
        open={open}
        onOk={handleSave}
        okText={t('eventSubscriptions.save')}
        confirmLoading={saving}
        onCancel={closeModal}
        destroyOnClose
      >
        <Form form={form} layout="vertical" initialValues={{ events: [], enabled: true }}>
          <Form.Item name="name" label={t('eventSubscriptions.name')} rules={[{ required: true }]}>
            <Input placeholder={t('eventSubscriptions.namePlaceholder')} />
          </Form.Item>
          <Form.Item name="url" label={t('eventSubscriptions.url')} rules={[{ required: true, type: 'url' }]}>
            <Input placeholder={t('eventSubscriptions.urlPlaceholder')} />
          </Form.Item>
          <Form.Item
            name="secret"
            label={t('eventSubscriptions.secret')}
            rules={editing ? [] : [{ required: true }]}
          >
            <Input.Password placeholder={t('eventSubscriptions.secretPlaceholder')} />
          </Form.Item>
          <Form.Item name="events" label={t('eventSubscriptions.events')} rules={[{ required: true }]}>
            <Select
              mode="tags"
              placeholder={t('eventSubscriptions.eventsPlaceholder')}
              options={KNOWN_EVENT_TYPES.map((eventType) => ({ value: eventType, label: eventType }))}
            />
          </Form.Item>
          <Form.Item name="enabled" label={t('eventSubscriptions.enabled')} valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
