import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, Select, Space, message, Tag, Card, List, Upload, InputNumber, Popconfirm, Alert } from 'antd';
import { PlusOutlined, DeleteOutlined, SearchOutlined, UploadOutlined, EyeOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { knowledgeApi } from '../api';
import { HelpLabel, HelpTooltip, PageHeader } from '../components/shared';

const { TextArea } = Input;

export default function KnowledgePage() {
  const { t } = useTranslation();
  const [sources, setSources] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [sourceModal, setSourceModal] = useState(false);
  const [kvModal, setKvModal] = useState(false);
  const [faqModal, setFaqModal] = useState(false);
  const [searchModal, setSearchModal] = useState(false);
  const [uploadModal, setUploadModal] = useState(false);
  const [searchResults, setSearchResults] = useState<any>(null);
  const [uploading, setUploading] = useState(false);
  const [chunkModal, setChunkModal] = useState(false);
  const [chunks, setChunks] = useState<any[]>([]);
  const [chunkLoading, setChunkLoading] = useState(false);
  const [chunkSourceName, setChunkSourceName] = useState('');
  const [sourceForm] = Form.useForm();
  const [kvForm] = Form.useForm();
  const [faqForm] = Form.useForm();
  const [searchForm] = Form.useForm();
  const [uploadForm] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      const res = await knowledgeApi.listSources();
      setSources(res.data);
    } catch (e: any) {
      message.error(t('knowledge.messages.loadSourcesFailed'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const errorDetail = (e: any) => e.response?.data?.detail || e.message || t('common.unknown');
  const sourceOptions = sources.map(s => ({
    value: s.id,
    label: `${s.name} (${t(`knowledge.sourceTypes.${s.source_type}`, { defaultValue: s.source_type })})`,
  }));

  const handleCreateSource = async () => {
    try {
      const values = await sourceForm.validateFields();
      await knowledgeApi.createSource(values);
      message.success(t('knowledge.messages.sourceCreated'));
      setSourceModal(false);
      sourceForm.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(`${t('knowledge.messages.createFailed')}: ${errorDetail(e)}`);
    }
  };

  const handleAddKV = async () => {
    try {
      const values = await kvForm.validateFields();
      await knowledgeApi.addKV(values);
      message.success(t('knowledge.messages.kvAdded'));
      setKvModal(false);
      kvForm.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(`${t('knowledge.messages.addFailed')}: ${errorDetail(e)}`);
    }
  };

  const handleAddFAQ = async () => {
    try {
      const values = await faqForm.validateFields();
      await knowledgeApi.addFAQ(values);
      message.success(t('knowledge.messages.faqAdded'));
      setFaqModal(false);
      faqForm.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(`${t('knowledge.messages.addFailed')}: ${errorDetail(e)}`);
    }
  };

  const handleSearch = async () => {
    try {
      const values = await searchForm.validateFields();
      const res = await knowledgeApi.search(values);
      setSearchResults(res.data);
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(`${t('knowledge.messages.searchFailed')}: ${errorDetail(e)}`);
    }
  };

  const handleUpload = async () => {
    try {
      const values = await uploadForm.validateFields();
      if (!values.file || values.file.length === 0) {
        message.error(t('knowledge.messages.chooseFile'));
        return;
      }
      const formData = new FormData();
      const uploadFile = values.file[0].originFileObj;
      formData.append('file', uploadFile);
      if (values.source_id) {
        formData.append('source_id', values.source_id);
      } else {
        formData.append('source_name', uploadFile.name.replace(/\.[^.]+$/, ''));
      }
      formData.append('domain', values.domain || 'default');
      formData.append('chunk_size', String(values.chunk_size || 500));
      formData.append('chunk_overlap', String(values.chunk_overlap || 50));

      setUploading(true);
      await knowledgeApi.upload(formData);
      message.success(t('knowledge.uploadSuccess'));
      setUploadModal(false);
      uploadForm.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(`${t('knowledge.messages.uploadFailed')}: ${errorDetail(e)}`);
    } finally {
      setUploading(false);
    }
  };

  const handleViewChunks = async (sourceId: string, sourceName: string) => {
    setChunkSourceName(sourceName);
    setChunkModal(true);
    setChunkLoading(true);
    try {
      const res = await knowledgeApi.listChunks(sourceId);
      setChunks(res.data);
    } catch (e: any) {
      message.error(`${t('knowledge.messages.loadChunksFailed')}: ${errorDetail(e)}`);
      setChunks([]);
    } finally {
      setChunkLoading(false);
    }
  };

  const handleDeleteSource = async (id: string) => {
    try {
      await knowledgeApi.deleteSource(id);
      message.success(t('knowledge.messages.deleted'));
      load();
    } catch (e: any) {
      message.error(`${t('knowledge.messages.deleteFailed')}: ${errorDetail(e)}`);
    }
  };

  const columns = [
    {
      title: t('common.name'), dataIndex: 'name', key: 'name',
      render: (v: string, record: any) => (
        <a onClick={() => handleViewChunks(record.id, v)}>{v}</a>
      ),
    },
    {
      title: <HelpLabel label={t('common.type')} help={t('knowledge.help.sourceType')} />, dataIndex: 'source_type', key: 'source_type',
      render: (v: string) => {
        const colors: Record<string, string> = { document: 'blue', faq: 'green', kv_entity: 'orange', structured_table: 'purple' };
        return <Tag color={colors[v] || 'default'}>{t(`knowledge.sourceTypes.${v}`, { defaultValue: v })}</Tag>;
      },
    },
    { title: <HelpLabel label={t('knowledge.domain')} help={t('knowledge.help.domain')} />, dataIndex: 'domain', key: 'domain' },
    { title: <HelpLabel label={t('knowledge.chunkCount')} help={t('knowledge.help.chunkCount')} />, dataIndex: 'chunk_count', key: 'chunk_count' },
    { title: t('common.status'), dataIndex: 'status', key: 'status', render: (v: string) => <Tag color={v === 'ready' ? 'green' : 'orange'}>{v}</Tag> },
    {
      title: t('common.actions'), key: 'actions', render: (_: any, record: any) => (
        <Space>
          <Button icon={<EyeOutlined />} size="small" onClick={() => handleViewChunks(record.id, record.name)}>{t('knowledge.viewChunks')}</Button>
          <Popconfirm title={t('knowledge.deleteConfirm')} onConfirm={() => handleDeleteSource(record.id)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
            <Button icon={<DeleteOutlined />} size="small" danger>{t('common.delete')}</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const readySources = sources.filter((s) => s.status === 'ready').length;
  const totalChunks = sources.reduce((sum, source) => sum + (source.chunk_count || 0), 0);

  return (
    <div>
      <PageHeader
        eyebrow={t('knowledge.eyebrow')}
        title={t('knowledge.title')}
        description={(
          <>
            {t('knowledge.description')}
            <HelpTooltip content={t('knowledge.help.page')} />
          </>
        )}
        status={t('knowledge.supportedFormats')}
        actions={(
          <>
            <Button icon={<SearchOutlined />} onClick={() => { setSearchResults(null); setSearchModal(true); }}>{t('knowledge.search')}</Button>
            <Button type="primary" icon={<UploadOutlined />} onClick={() => { uploadForm.resetFields(); setUploadModal(true); }}>{t('knowledge.upload')}</Button>
            <Button onClick={() => { kvForm.resetFields(); setKvModal(true); }}>{t('knowledge.addKV')}</Button>
            <Button onClick={() => { faqForm.resetFields(); setFaqModal(true); }}>{t('knowledge.addFAQ')}</Button>
            <Button icon={<PlusOutlined />} onClick={() => { sourceForm.resetFields(); setSourceModal(true); }}>{t('knowledge.addSource')}</Button>
          </>
        )}
      />

      <div className="aezab-summary-grid">
        <div className="aezab-stat-card">
          <div className="aezab-stat-label">{t('knowledge.totalSources')}</div>
          <div className="aezab-stat-value">{sources.length}</div>
        </div>
        <div className="aezab-stat-card">
          <div className="aezab-stat-label">{t('knowledge.readySources')}</div>
          <div className="aezab-stat-value">{readySources}</div>
        </div>
        <div className="aezab-stat-card">
          <div className="aezab-stat-label">{t('knowledge.totalChunks')}</div>
          <div className="aezab-stat-value">{totalChunks}</div>
        </div>
        <div className="aezab-stat-card">
          <div className="aezab-stat-label">
            <HelpLabel label={t('knowledge.ingestionStandard')} help={t('knowledge.help.ingestionStandard')} />
          </div>
          <div className="aezab-muted">{t('knowledge.ingestionStandardValue')}</div>
        </div>
      </div>

      <div className="aezab-table-card">
        <Table
          dataSource={sources}
          columns={columns}
          rowKey="id"
          loading={loading}
          pagination={{ pageSize: 10 }}
          locale={{ emptyText: t('knowledge.emptySources') }}
        />
      </div>

      {/* Create source */}
      <Modal title={t('knowledge.addSource')} open={sourceModal} onOk={handleCreateSource} onCancel={() => setSourceModal(false)} okText={t('common.save')} cancelText={t('common.cancel')}>
        <Form form={sourceForm} layout="vertical">
          <Form.Item name="name" label={t('common.name')} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="source_type" label={<HelpLabel label={t('common.type')} help={t('knowledge.help.sourceType')} />} rules={[{ required: true }]}>
            <Select options={[
              { value: 'document', label: t('knowledge.sourceTypes.document') },
              { value: 'faq', label: t('knowledge.sourceTypes.faq') },
              { value: 'kv_entity', label: t('knowledge.sourceTypes.kv_entity') },
              { value: 'structured_table', label: t('knowledge.sourceTypes.structured_table') },
            ]} />
          </Form.Item>
          <Form.Item name="domain" label={<HelpLabel label={t('knowledge.domain')} help={t('knowledge.help.domain')} />} initialValue="default"><Input /></Form.Item>
        </Form>
      </Modal>

      {/* Add KV */}
      <Modal title={t('knowledge.addKVTitle')} open={kvModal} onOk={handleAddKV} onCancel={() => setKvModal(false)} okText={t('common.save')} cancelText={t('common.cancel')}>
        <Form form={kvForm} layout="vertical">
          <Form.Item name="source_id" label={<HelpLabel label={t('knowledge.sources')} help={t('knowledge.help.source')} />} rules={[{ required: true }]}>
            <Select placeholder={t('knowledge.selectSource')} options={sourceOptions} />
          </Form.Item>
          <Form.Item name="entity_key" label={<HelpLabel label={t('knowledge.entityKey')} help={t('knowledge.help.entityKey')} />} rules={[{ required: true }]}><Input placeholder={t('knowledge.entityKeyPlaceholder')} /></Form.Item>
          <Form.Item name="content" label={t('knowledge.content')} rules={[{ required: true }]}><TextArea placeholder={t('knowledge.contentPlaceholder')} /></Form.Item>
          <Form.Item name="domain" label={<HelpLabel label={t('knowledge.domain')} help={t('knowledge.help.domain')} />} initialValue="default"><Input /></Form.Item>
        </Form>
      </Modal>

      {/* Add FAQ */}
      <Modal title={t('knowledge.addFAQ')} open={faqModal} onOk={handleAddFAQ} onCancel={() => setFaqModal(false)} okText={t('common.save')} cancelText={t('common.cancel')}>
        <Form form={faqForm} layout="vertical">
          <Form.Item name="source_id" label={<HelpLabel label={t('knowledge.sources')} help={t('knowledge.help.source')} />} rules={[{ required: true }]}>
            <Select placeholder={t('knowledge.selectSource')} options={sourceOptions} />
          </Form.Item>
          <Form.Item name="question" label={<HelpLabel label={t('knowledge.question')} help={t('knowledge.help.faq')} />} rules={[{ required: true }]}><Input placeholder={t('knowledge.questionPlaceholder')} /></Form.Item>
          <Form.Item name="answer" label={t('knowledge.answer')} rules={[{ required: true }]}><TextArea rows={4} /></Form.Item>
          <Form.Item name="domain" label={<HelpLabel label={t('knowledge.domain')} help={t('knowledge.help.domain')} />} initialValue="default"><Input /></Form.Item>
        </Form>
      </Modal>

      {/* Upload document */}
      <Modal title={t('knowledge.upload')} open={uploadModal} onOk={handleUpload} onCancel={() => setUploadModal(false)} confirmLoading={uploading} okText={t('knowledge.upload')} cancelText={t('common.cancel')}>
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={t('knowledge.uploadStandard')}
          description={t('knowledge.uploadStandardDesc')}
        />
        <Form form={uploadForm} layout="vertical">
          <Form.Item name="source_id" label={<HelpLabel label={t('knowledge.sources')} help={t('knowledge.help.source')} />}>
            <Select
              allowClear
              placeholder={t('knowledge.autoCreateSource')}
              options={sourceOptions}
            />
          </Form.Item>
          <Form.Item name="file" label={<HelpLabel label={t('knowledge.file')} help={t('knowledge.help.file')} />} valuePropName="fileList" getValueFromEvent={(e: any) => e?.fileList} rules={[{ required: true }]}>
            <Upload beforeUpload={() => false} maxCount={1} accept=".txt,.md,.pdf,.docx,.csv,.xlsx">
              <Button icon={<UploadOutlined />}>{t('knowledge.chooseFile')}</Button>
            </Upload>
          </Form.Item>
          <Form.Item name="domain" label={<HelpLabel label={t('knowledge.domain')} help={t('knowledge.help.domain')} />} initialValue="default"><Input /></Form.Item>
          <Form.Item name="chunk_size" label={<HelpLabel label={t('knowledge.chunkSizeWithUnit')} help={t('knowledge.help.chunkSize')} />} initialValue={500}>
            <InputNumber min={100} max={5000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="chunk_overlap" label={<HelpLabel label={t('knowledge.chunkOverlapWithUnit')} help={t('knowledge.help.chunkOverlap')} />} initialValue={50}>
            <InputNumber min={0} max={500} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Chunk viewer */}
      <Modal
        title={`${t('knowledge.viewChunks')} - ${chunkSourceName}`}
        open={chunkModal}
        onCancel={() => setChunkModal(false)}
        footer={null}
        width={860}
      >
        <Table
          dataSource={chunks}
          rowKey="id"
          loading={chunkLoading}
          size="small"
          pagination={{ pageSize: 10 }}
          columns={[
            { title: t('knowledge.entityKey'), dataIndex: 'entity_key', key: 'entity_key', width: 160, ellipsis: true },
            {
              title: t('knowledge.content'), dataIndex: 'content', key: 'content',
              ellipsis: true,
              render: (v: string) => (
                <span title={v}>{v && v.length > 120 ? v.slice(0, 120) + '...' : v}</span>
              ),
            },
            { title: <HelpLabel label={t('knowledge.domain')} help={t('knowledge.help.domain')} />, dataIndex: 'domain', key: 'domain', width: 100 },
          ]}
          locale={{ emptyText: t('knowledge.emptyChunks') }}
        />
      </Modal>

      {/* Search test */}
      <Modal
        title={<HelpLabel label={t('knowledge.search')} help={t('knowledge.help.search')} />}
        open={searchModal}
        onCancel={() => setSearchModal(false)}
        footer={null}
        width={720}
      >
        <Form form={searchForm} layout="inline" onFinish={handleSearch} style={{ marginBottom: 16 }}>
          <Form.Item name="query" rules={[{ required: true }]}>
            <Input placeholder={t('knowledge.searchPlaceholder')} style={{ width: 400 }} />
          </Form.Item>
          <Form.Item name="top_k" initialValue={5}>
            <Select style={{ width: 80 }} options={[3, 5, 10].map(n => ({ value: n, label: `Top ${n}` }))} />
          </Form.Item>
          <Button type="primary" htmlType="submit">{t('common.search')}</Button>
        </Form>
        {searchResults && (
          <Card size="small" title={`${t('knowledge.results')} (${searchResults.hits?.length || 0} hits, ${searchResults.latency_ms?.toFixed(1)}ms)`}>
            {searchResults.fast_answer && <p><strong>{t('knowledge.fastAnswer')}:</strong> {searchResults.fast_answer}</p>}
            <List
              size="small"
              dataSource={searchResults.hits || []}
              renderItem={(hit: any) => (
                <List.Item>
                  <List.Item.Meta
                    title={`[${hit.channel}] ${hit.source_name} (score: ${hit.score})`}
                    description={hit.content}
                  />
                </List.Item>
              )}
            />
          </Card>
        )}
      </Modal>
    </div>
  );
}
