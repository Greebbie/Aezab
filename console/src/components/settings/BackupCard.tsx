import { useEffect, useState } from 'react';
import { Alert, Button, Card, Popconfirm, Space, Table, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { CloudDownloadOutlined, DeleteOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { backupApi } from '../../api';
import type { BackupSummary } from '../../types';

// Bilingual copy local to this card, mirroring SettingsPage.tsx's own
// SETTINGS_COPY constant rather than migrating this page to the shared
// i18n JSON files (see console/src/pages/SettingsPage.tsx for the pattern
// this follows).
const BACKUP_COPY = {
  zh: {
    title: '数据备份',
    description:
      '系统会按设定的间隔自动把数据库、向量索引等本地数据打包备份到服务器本地，' +
      '只保留最近若干份，更旧的会自动清理。强烈建议定期点击下载，把备份文件另存到' +
      '你自己的电脑或云盘上——服务器本地备份和原始数据在同一块磁盘，磁盘整体损坏时' +
      '无法起到异地容灾的作用。',
    backupNow: '立即备份',
    refresh: '刷新',
    columnName: '备份文件',
    columnCreatedAt: '创建时间',
    columnSize: '大小',
    columnActions: '操作',
    download: '下载',
    delete: '删除',
    deleteConfirmTitle: '删除这份备份？',
    deleteConfirmDescription: '删除后无法恢复，请确认已经下载过需要的数据。',
    empty: '还没有备份',
    backupSuccess: '备份创建成功',
    backupFailed: '创建备份失败',
    loadFailed: '加载备份列表失败',
    downloadFailed: '下载备份失败',
    deleteSuccess: '备份已删除',
    deleteFailed: '删除备份失败',
  },
  en: {
    title: 'Data Backups',
    description:
      'The system automatically packages the database, vector index, and other local ' +
      'data into a backup on this server on a fixed schedule, keeping only the most ' +
      'recent few and cleaning up older ones. It is strongly recommended to download ' +
      'backups regularly and save them to your own computer or cloud storage — local ' +
      'backups live on the same disk as the original data, so they will not survive a ' +
      'full disk failure.',
    backupNow: 'Backup Now',
    refresh: 'Refresh',
    columnName: 'Backup File',
    columnCreatedAt: 'Created At',
    columnSize: 'Size',
    columnActions: 'Actions',
    download: 'Download',
    delete: 'Delete',
    deleteConfirmTitle: 'Delete this backup?',
    deleteConfirmDescription: 'This cannot be undone — make sure you have already downloaded a copy if you need one.',
    empty: 'No backups yet',
    backupSuccess: 'Backup created successfully',
    backupFailed: 'Failed to create backup',
    loadFailed: 'Failed to load backups',
    downloadFailed: 'Failed to download backup',
    deleteSuccess: 'Backup deleted',
    deleteFailed: 'Failed to delete backup',
  },
} as const;

type BackupCopy = (typeof BACKUP_COPY)[keyof typeof BACKUP_COPY];

function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${exponent === 0 ? value : value.toFixed(1)} ${units[exponent]}`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export default function BackupCard() {
  const { i18n } = useTranslation();
  const copy: BackupCopy = i18n.language === 'zh' ? BACKUP_COPY.zh : BACKUP_COPY.en;

  const [backups, setBackups] = useState<BackupSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [downloadingName, setDownloadingName] = useState<string | null>(null);
  const [deletingName, setDeletingName] = useState<string | null>(null);

  const loadBackups = async () => {
    setLoading(true);
    try {
      const res = await backupApi.list();
      setBackups(res.data);
    } catch (error: unknown) {
      message.error(errorMessage(error, copy.loadFailed));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadBackups();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleBackupNow = async () => {
    setCreating(true);
    try {
      await backupApi.create();
      message.success(copy.backupSuccess);
      await loadBackups();
    } catch (error: unknown) {
      message.error(errorMessage(error, copy.backupFailed));
    } finally {
      setCreating(false);
    }
  };

  const handleDownload = async (name: string) => {
    setDownloadingName(name);
    try {
      const res = await backupApi.downloadBlob(name);
      triggerBlobDownload(res.data, name);
    } catch (error: unknown) {
      message.error(errorMessage(error, copy.downloadFailed));
    } finally {
      setDownloadingName(null);
    }
  };

  const handleDelete = async (name: string) => {
    setDeletingName(name);
    try {
      await backupApi.delete(name);
      message.success(copy.deleteSuccess);
      await loadBackups();
    } catch (error: unknown) {
      message.error(errorMessage(error, copy.deleteFailed));
    } finally {
      setDeletingName(null);
    }
  };

  const columns: ColumnsType<BackupSummary> = [
    { title: copy.columnName, dataIndex: 'name', key: 'name' },
    {
      title: copy.columnCreatedAt,
      dataIndex: 'created_at',
      key: 'created_at',
      render: (value: string) => new Date(value).toLocaleString(),
    },
    {
      title: copy.columnSize,
      dataIndex: 'size_bytes',
      key: 'size_bytes',
      render: (value: number) => formatBytes(value),
    },
    {
      title: copy.columnActions,
      key: 'actions',
      render: (_: unknown, record: BackupSummary) => (
        <Space>
          <Button
            size="small"
            icon={<CloudDownloadOutlined />}
            loading={downloadingName === record.name}
            onClick={() => handleDownload(record.name)}
          >
            {copy.download}
          </Button>
          <Popconfirm
            title={copy.deleteConfirmTitle}
            description={copy.deleteConfirmDescription}
            onConfirm={() => handleDelete(record.name)}
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />} loading={deletingName === record.name}>
              {copy.delete}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={copy.title}
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadBackups} loading={loading}>
            {copy.refresh}
          </Button>
          <Button type="primary" icon={<SaveOutlined />} onClick={handleBackupNow} loading={creating}>
            {copy.backupNow}
          </Button>
        </Space>
      }
      style={{ marginBottom: 24 }}
    >
      <Alert type="info" showIcon message={copy.description} style={{ marginBottom: 16 }} />
      <Table
        rowKey="name"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={backups}
        locale={{ emptyText: copy.empty }}
        pagination={backups.length > 10 ? { pageSize: 10 } : false}
      />
    </Card>
  );
}
