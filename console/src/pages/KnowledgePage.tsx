import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, Select, Space, message, Tag, Card, List, Upload, InputNumber, Popconfirm } from 'antd';
import { PlusOutlined, DeleteOutlined, SearchOutlined, UploadOutlined, EyeOutlined } from '@ant-design/icons';
import { knowledgeApi } from '../api';

const { TextArea } = Input;

export default function KnowledgePage() {
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
      message.error('加载知识源列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const sourceOptions = sources.map(s => ({ value: s.id, label: `${s.name} (${s.source_type})` }));

  const handleCreateSource = async () => {
    try {
      const values = await sourceForm.validateFields();
      await knowledgeApi.createSource(values);
      message.success('知识源已创建');
      setSourceModal(false);
      sourceForm.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error('创建失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const handleAddKV = async () => {
    try {
      const values = await kvForm.validateFields();
      await knowledgeApi.addKV(values);
      message.success('实体已添加');
      setKvModal(false);
      kvForm.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error('添加失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const handleAddFAQ = async () => {
    try {
      const values = await faqForm.validateFields();
      await knowledgeApi.addFAQ(values);
      message.success('FAQ 已添加');
      setFaqModal(false);
      faqForm.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error('添加失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const handleSearch = async () => {
    try {
      const values = await searchForm.validateFields();
      const res = await knowledgeApi.search(values);
      setSearchResults(res.data);
    } catch (e: any) {
      if (e.errorFields) return;
      message.error('检索失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const handleUpload = async () => {
    try {
      const values = await uploadForm.validateFields();
      if (!values.file || values.file.length === 0) {
        message.error('请选择文件');
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
      message.success('文档上传并分块成功');
      setUploadModal(false);
      uploadForm.resetFields();
      load();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error('上传失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
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
      message.error('加载条目失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
      setChunks([]);
    } finally {
      setChunkLoading(false);
    }
  };

  const handleDeleteSource = async (id: string) => {
    try {
      await knowledgeApi.deleteSource(id);
      message.success('已删除');
      load();
    } catch (e: any) {
      message.error('删除失败: ' + (e.response?.data?.detail || e.message || '未知错误'));
    }
  };

  const columns = [
    {
      title: '名称', dataIndex: 'name', key: 'name',
      render: (v: string, record: any) => (
        <a onClick={() => handleViewChunks(record.id, v)}>{v}</a>
      ),
    },
    {
      title: '类型', dataIndex: 'source_type', key: 'source_type',
      render: (v: string) => {
        const colors: Record<string, string> = { document: 'blue', faq: 'green', kv_entity: 'orange', structured_table: 'purple' };
        return <Tag color={colors[v] || 'default'}>{v}</Tag>;
      },
    },
    { title: '域', dataIndex: 'domain', key: 'domain' },
    { title: '条目数', dataIndex: 'chunk_count', key: 'chunk_count' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v: string) => <Tag color={v === 'ready' ? 'green' : 'orange'}>{v}</Tag> },
    {
      title: '操作', key: 'actions', render: (_: any, record: any) => (
        <Space>
          <Button icon={<EyeOutlined />} size="small" onClick={() => handleViewChunks(record.id, record.name)}>查看</Button>
          <Popconfirm title="确认删除此知识源及其所有条目？" onConfirm={() => handleDeleteSource(record.id)} okText="确认" cancelText="取消">
            <Button icon={<DeleteOutlined />} size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>知识管理</h2>
        <Space>
          <Button icon={<SearchOutlined />} onClick={() => { setSearchResults(null); setSearchModal(true); }}>检索测试</Button>
          <Button icon={<UploadOutlined />} onClick={() => { uploadForm.resetFields(); setUploadModal(true); }}>上传文档</Button>
          <Button onClick={() => { kvForm.resetFields(); setKvModal(true); }}>添加 KV 实体</Button>
          <Button onClick={() => { faqForm.resetFields(); setFaqModal(true); }}>添加 FAQ</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { sourceForm.resetFields(); setSourceModal(true); }}>创建知识源</Button>
        </Space>
      </div>

      <Table dataSource={sources} columns={columns} rowKey="id" loading={loading} />

      {/* Create source */}
      <Modal title="创建知识源" open={sourceModal} onOk={handleCreateSource} onCancel={() => setSourceModal(false)}>
        <Form form={sourceForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="source_type" label="类型" rules={[{ required: true }]}>
            <Select options={[
              { value: 'document', label: '文档' },
              { value: 'faq', label: 'FAQ' },
              { value: 'kv_entity', label: 'KV实体表' },
              { value: 'structured_table', label: '结构化表' },
            ]} />
          </Form.Item>
          <Form.Item name="domain" label="知识域" initialValue="default"><Input /></Form.Item>
        </Form>
      </Modal>

      {/* Add KV */}
      <Modal title="添加 KV 实体 (快答通道)" open={kvModal} onOk={handleAddKV} onCancel={() => setKvModal(false)}>
        <Form form={kvForm} layout="vertical">
          <Form.Item name="source_id" label="知识源" rules={[{ required: true }]}>
            <Select placeholder="选择知识源" options={sourceOptions} />
          </Form.Item>
          <Form.Item name="entity_key" label="关键词/实体名" rules={[{ required: true }]}><Input placeholder="如: 市民中心电话" /></Form.Item>
          <Form.Item name="content" label="值/答案" rules={[{ required: true }]}><TextArea placeholder="0571-12345678" /></Form.Item>
          <Form.Item name="domain" label="知识域" initialValue="default"><Input /></Form.Item>
        </Form>
      </Modal>

      {/* Add FAQ */}
      <Modal title="添加 FAQ" open={faqModal} onOk={handleAddFAQ} onCancel={() => setFaqModal(false)}>
        <Form form={faqForm} layout="vertical">
          <Form.Item name="source_id" label="知识源" rules={[{ required: true }]}>
            <Select placeholder="选择知识源" options={sourceOptions} />
          </Form.Item>
          <Form.Item name="question" label="问题" rules={[{ required: true }]}><Input placeholder="办居住证需要什么材料？" /></Form.Item>
          <Form.Item name="answer" label="答案" rules={[{ required: true }]}><TextArea rows={4} /></Form.Item>
          <Form.Item name="domain" label="知识域" initialValue="default"><Input /></Form.Item>
        </Form>
      </Modal>

      {/* Upload document */}
      <Modal title="上传文档" open={uploadModal} onOk={handleUpload} onCancel={() => setUploadModal(false)} confirmLoading={uploading}>
        <Form form={uploadForm} layout="vertical">
          <Form.Item name="source_id" label="知识源">
            <Select
              allowClear
              placeholder="不选则自动按文件名创建"
              options={sourceOptions}
            />
          </Form.Item>
          <Form.Item name="file" label="文件" valuePropName="fileList" getValueFromEvent={(e: any) => e?.fileList} rules={[{ required: true }]}>
            <Upload beforeUpload={() => false} maxCount={1} accept=".txt,.md,.pdf,.docx">
              <Button icon={<UploadOutlined />}>选择文件 (.txt, .md, .pdf, .docx)</Button>
            </Upload>
          </Form.Item>
          <Form.Item name="domain" label="知识域" initialValue="default"><Input /></Form.Item>
          <Form.Item name="chunk_size" label="分块大小 (字符)" initialValue={500}>
            <InputNumber min={100} max={5000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="chunk_overlap" label="分块重叠 (字符)" initialValue={50}>
            <InputNumber min={0} max={500} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Chunk viewer */}
      <Modal
        title={`条目查看 — ${chunkSourceName}`}
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
            { title: '关键词', dataIndex: 'entity_key', key: 'entity_key', width: 160, ellipsis: true },
            {
              title: '内容', dataIndex: 'content', key: 'content',
              ellipsis: true,
              render: (v: string) => (
                <span title={v}>{v && v.length > 120 ? v.slice(0, 120) + '...' : v}</span>
              ),
            },
            { title: '域', dataIndex: 'domain', key: 'domain', width: 100 },
          ]}
          locale={{ emptyText: '该知识源暂无条目' }}
        />
      </Modal>

      {/* Search test */}
      <Modal title="检索测试" open={searchModal} onCancel={() => setSearchModal(false)} footer={null} width={720}>
        <Form form={searchForm} layout="inline" onFinish={handleSearch} style={{ marginBottom: 16 }}>
          <Form.Item name="query" rules={[{ required: true }]}>
            <Input placeholder="输入查询语句" style={{ width: 400 }} />
          </Form.Item>
          <Form.Item name="top_k" initialValue={5}>
            <Select style={{ width: 80 }} options={[3, 5, 10].map(n => ({ value: n, label: `Top ${n}` }))} />
          </Form.Item>
          <Button type="primary" htmlType="submit">搜索</Button>
        </Form>
        {searchResults && (
          <Card size="small" title={`结果 (${searchResults.hits?.length || 0} hits, ${searchResults.latency_ms?.toFixed(1)}ms)`}>
            {searchResults.fast_answer && <p><strong>快答:</strong> {searchResults.fast_answer}</p>}
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
