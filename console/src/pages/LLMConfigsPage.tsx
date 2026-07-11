import { useEffect, useState } from 'react';
import { Table, Button, Space, message, Tag, Popconfirm } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { PlusOutlined, EditOutlined, DeleteOutlined, CrownOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { llmConfigApi } from '../api';
import { friendlyError } from '../utils/friendlyError';
import LLMConfigModal, { LLMConfigWithTopP } from '../components/settings/LLMConfigModal';

export default function LLMConfigsPage() {
  const { t } = useTranslation();
  const [configs, setConfigs] = useState<LLMConfigWithTopP[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<LLMConfigWithTopP | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await llmConfigApi.list();
      setConfigs(res.data as LLMConfigWithTopP[]);
    } catch (err) {
      message.error(friendlyError(err, t));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };

  const openEdit = (record: LLMConfigWithTopP) => {
    setEditing(record);
    setModalOpen(true);
  };

  const handleModalSaved = () => {
    setModalOpen(false);
    setEditing(null);
    load();
  };

  const handleDelete = async (id: string) => {
    try {
      await llmConfigApi.delete(id);
      message.success(t('llmConfigs.deleteSuccess'));
      load();
    } catch (err) {
      message.error(friendlyError(err, t));
    }
  };

  const handleSetDefault = async (id: string) => {
    try {
      await llmConfigApi.setDefault(id);
      message.success(t('llmConfigs.setDefaultSuccess'));
      load();
    } catch (err) {
      message.error(friendlyError(err, t));
    }
  };

  const providerColor = (provider: string) => {
    switch (provider) {
      case 'openai_compatible': return 'green';
      case 'dashscope': return 'blue';
      case 'zhipu': return 'purple';
      case 'local': return 'orange';
      default: return 'default';
    }
  };

  const columns: ColumnsType<LLMConfigWithTopP> = [
    {
      title: t('common.name'),
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record) => (
        <Space>
          {name}
          {record.is_default && <Tag color="gold" icon={<CrownOutlined />}>{t('llmConfigs.defaultTag')}</Tag>}
        </Space>
      ),
    },
    {
      title: t('llmConfigs.provider'),
      dataIndex: 'provider',
      key: 'provider',
      render: (v: string) => <Tag color={providerColor(v)}>{v}</Tag>,
    },
    { title: t('llmConfigs.model'), dataIndex: 'model', key: 'model' },
    { title: t('llmConfigs.baseUrl'), dataIndex: 'base_url', key: 'base_url', ellipsis: true },
    {
      title: t('llmConfigs.temperature'),
      dataIndex: 'temperature',
      key: 'temperature',
      width: 100,
    },
    {
      title: t('llmConfigs.maxTokens'),
      dataIndex: 'max_tokens',
      key: 'max_tokens',
      width: 100,
    },
    {
      title: t('llmConfigs.columnCreated'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (v: string) => v ? new Date(v).toLocaleString() : '-',
    },
    {
      title: t('common.actions'),
      key: 'actions',
      width: 280,
      render: (_, record) => (
        <Space>
          {!record.is_default && (
            <Button
              icon={<CrownOutlined />}
              size="small"
              onClick={() => handleSetDefault(record.id)}
            >
              {t('llmConfigs.setDefault')}
            </Button>
          )}
          <Button
            icon={<EditOutlined />}
            size="small"
            onClick={() => openEdit(record)}
          >
            {t('common.edit')}
          </Button>
          <Popconfirm
            title={t('llmConfigs.deleteConfirm')}
            description={t('llmConfigs.deleteConfirmDescription')}
            onConfirm={() => handleDelete(record.id)}
            okText={t('common.delete')}
            cancelText={t('common.cancel')}
            okButtonProps={{ danger: true }}
          >
            <Button icon={<DeleteOutlined />} size="small" danger>
              {t('common.delete')}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>{t('llmConfigs.title')}</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          {t('llmConfigs.create')}
        </Button>
      </div>

      <Table
        dataSource={configs}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 10 }}
      />

      <LLMConfigModal
        open={modalOpen}
        editing={editing}
        onClose={() => { setModalOpen(false); setEditing(null); }}
        onSaved={handleModalSaved}
      />
    </div>
  );
}
