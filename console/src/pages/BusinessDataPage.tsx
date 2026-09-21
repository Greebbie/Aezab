import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Alert, Button, Card, Checkbox, Empty, Form, Input, InputNumber, Modal,
  Popconfirm, Radio, Select, Space, Spin, Switch, Table, Tabs, Tag, Typography, Upload, message,
} from 'antd';
import { DeleteOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons';
import { authApi, businessDataApi } from '../api';
import { useAuth } from '../AuthGate';
import { PageHeader } from '../components/shared';
import { friendlyError } from '../utils/friendlyError';
import type { BusinessField, BusinessQueryResult, BusinessRecord, BusinessRecordPage, BusinessSource, BusinessSourceInput, BusinessValues } from '../types';

const { Text } = Typography;
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const PAGE_SIZE = 20;
const emptyRecords: BusinessRecordPage = { items: [], total: 0, offset: 0, limit: PAGE_SIZE };

export default function BusinessDataPage() {
  const { t } = useTranslation();
  const { authEnabled } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [role, setRole] = useState<string | null>(authEnabled ? null : 'admin');
  const [sources, setSources] = useState<BusinessSource[]>([]);
  const [selectedId, setSelectedId] = useState<string | undefined>(searchParams.get('source') || undefined);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [records, setRecords] = useState(emptyRecords);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsLoaded, setRecordsLoaded] = useState(false);
  const [tab, setTab] = useState('records');
  const [queryResult, setQueryResult] = useState<BusinessQueryResult | null>(null);
  const [queryLoading, setQueryLoading] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [editingSource, setEditingSource] = useState<BusinessSource | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [recordOpen, setRecordOpen] = useState(false);
  const [editingRecord, setEditingRecord] = useState<BusinessRecord | null>(null);
  const [recordError, setRecordError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState(false);
  const [sourceForm] = Form.useForm<BusinessSourceInput>();
  const [recordForm] = Form.useForm<BusinessValues>();
  const [queryForm] = Form.useForm<BusinessValues>();
  const request = useRef(0);
  const queryRequest = useRef(0);
  const selection = useRef<string>();
  const selected = sources.find((source) => source.id === selectedId);
  const canEdit = role === 'admin' || role === 'editor';
  const canConfigure = canEdit && (selected?.kind !== 'postgres' || role === 'admin');
  const kind = Form.useWatch('kind', sourceForm) || 'table';
  const fields = Form.useWatch('fields', sourceForm) || [];
  const filterFields = Form.useWatch('filter_fields', sourceForm) || [];
  const schemaLocked = editingSource?.kind === 'table' && records.total > 0;

  useEffect(() => {
    if (authEnabled) authApi.me().then((res) => setRole(res.data.role)).catch((e) => setError(friendlyError(e, t)));
  }, [authEnabled, t]);

  const loadSources = useCallback(async () => {
    setLoading(true);
    try {
      const result = await businessDataApi.list();
      setSources(result.data);
      setSelectedId((current) => result.data.some((source) => source.id === current) ? current : result.data[0]?.id);
    } catch (e) { setError(friendlyError(e, t)); }
    finally { setLoading(false); }
  }, [t]);

  useEffect(() => { loadSources(); }, [loadSources]);

  const loadRecords = useCallback(async (id: string, offset = 0) => {
    const generation = ++request.current;
    setRecordsLoading(true);
    try {
      const result = await businessDataApi.records(id, { offset, limit: PAGE_SIZE });
      if (generation !== request.current || selection.current !== id) return;
      setRecords(result.data);
      setRecordsLoaded(true);
    } catch (e) {
      if (generation === request.current) setError(friendlyError(e, t));
    } finally { if (generation === request.current) setRecordsLoading(false); }
  }, [t]);

  useEffect(() => {
    selection.current = selectedId;
    request.current += 1;
    queryRequest.current += 1;
    setRecords(emptyRecords);
    setRecordsLoaded(false);
    setRecordsLoading(false);
    setQueryResult(null);
    setQueryLoading(false);
    setError(null);
    queryForm.resetFields();
    if (selected?.kind === 'table') {
      setTab('records');
      loadRecords(selected.id);
    } else setTab('query');
    return () => { request.current += 1; queryRequest.current += 1; };
  }, [selectedId, selected?.kind, loadRecords, queryForm]);

  const openSource = (source: BusinessSource | null) => {
    setEditingSource(source);
    setSourceError(null);
    sourceForm.resetFields();
    sourceForm.setFieldsValue(source ? { ...source, postgres_dsn: undefined } : {
      name: '', description: '', kind: 'table', fields: [{ name: '', type: 'string', required: true }],
      filter_fields: [], required_filters: [], max_rows: 20, enabled: true, postgres_schema: 'public',
    });
    setSourceOpen(true);
  };

  const saveSource = async () => {
    try {
      const values = await sourceForm.validateFields();
      setSaving(true);
      setSourceError(null);
      const body: BusinessSourceInput = { ...values, fields: values.fields.map((field) => ({ ...field, required: !!field.required })) };
      if (!body.postgres_dsn?.trim()) delete body.postgres_dsn;
      if (body.kind === 'table') {
        delete body.postgres_dsn; delete body.postgres_schema; delete body.postgres_table; delete body.tenant_column;
      } else body.tenant_column = body.tenant_column || null;
      let result;
      if (editingSource) {
        const { kind: _kind, ...updates } = body;
        result = await businessDataApi.update(editingSource.id, updates);
      } else result = await businessDataApi.create(body);
      await loadSources();
      setSelectedId(result.data.id);
      setSearchParams({ source: result.data.id }, { replace: true });
      setSourceOpen(false);
      sourceForm.resetFields();
      queryForm.resetFields();
      setQueryResult(null);
      message.success(t('businessData.saved'));
    } catch (e: any) {
      if (!e.errorFields) setSourceError(friendlyError(e, t));
    } finally { setSaving(false); }
  };

  const deleteSource = async () => {
    if (!selected) return;
    try { await businessDataApi.delete(selected.id); await loadSources(); }
    catch (e) { setError(friendlyError(e, t)); }
  };

  const openRecord = (record: BusinessRecord | null) => {
    setEditingRecord(record); setRecordError(null); recordForm.resetFields();
    if (record) recordForm.setFieldsValue(record.values);
    setRecordOpen(true);
  };

  const saveRecord = async () => {
    if (!selected) return;
    try {
      const values = await recordForm.validateFields();
      setSaving(true); setRecordError(null);
      const normalized = Object.fromEntries(selected.fields.map((field) => [field.name,
        values[field.name] === '' ? null : values[field.name] ?? null]));
      if (editingRecord) await businessDataApi.updateRecord(selected.id, editingRecord.id, normalized);
      else await businessDataApi.createRecord(selected.id, normalized);
      setRecordOpen(false); setQueryResult(null);
      await loadRecords(selected.id, editingRecord ? records.offset : 0);
    } catch (e: any) { if (!e.errorFields) setRecordError(friendlyError(e, t)); }
    finally { setSaving(false); }
  };

  const deleteRecord = async (record: BusinessRecord) => {
    if (!selected) return;
    try {
      await businessDataApi.deleteRecord(selected.id, record.id);
      setQueryResult(null);
      await loadRecords(selected.id, records.items.length === 1 ? Math.max(0, records.offset - PAGE_SIZE) : records.offset);
    } catch (e) { setError(friendlyError(e, t)); }
  };

  const importCsv = async (file: File) => {
    if (!selected) return;
    if (file.size > 2 * 1024 * 1024) { setError(t('businessData.csvHelp')); return; }
    const id = selected.id;
    setImporting(true); setError(null);
    try {
      await businessDataApi.importCsv(id, file);
      if (selection.current === id) { setQueryResult(null); await loadRecords(id); }
      message.success(t('businessData.imported'));
    } catch (e) { setError(friendlyError(e, t)); }
    finally { setImporting(false); }
  };

  const runQuery = async () => {
    if (!selected) return;
    const generation = ++queryRequest.current;
    try {
      const values = await queryForm.validateFields();
      const id = selected.id;
      setQueryLoading(true); setError(null);
      const filters = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== ''));
      const result = await businessDataApi.query(id, filters, selected.max_rows);
      if (selection.current === id && generation === queryRequest.current) setQueryResult(result.data);
    } catch (e: any) { if (!e.errorFields && generation === queryRequest.current) setError(friendlyError(e, t)); }
    finally { if (generation === queryRequest.current) setQueryLoading(false); }
  };

  const fieldInput = (field: BusinessField) => field.type === 'boolean'
    ? <Radio.Group options={[{ value: true, label: t('businessData.true') }, { value: false, label: t('businessData.false') }]} />
    : field.type === 'integer' || field.type === 'number'
      ? <InputNumber style={{ width: '100%' }} precision={field.type === 'integer' ? 0 : undefined} />
      : <Input type={field.type === 'date' ? 'date' : 'text'} />;
  const formatValue = (value: unknown) => value == null ? '—' : String(value);
  const dataColumns = selected?.fields.map((field) => ({
    title: field.name, key: field.name, dataIndex: ['values', field.name], width: 160,
    render: formatValue,
  })) || [];
  const fieldOptions = fields.filter((field: BusinessField) => field?.name).map((field: BusinessField) => ({ value: field.name, label: field.name }));

  return <div>
    <PageHeader title={t('businessData.title')} description={t('businessData.description')}
      actions={<Button type="primary" icon={<PlusOutlined />} disabled={!canEdit} onClick={() => openSource(null)}>{t('businessData.create')}</Button>} />
    {error && <Alert type="error" showIcon message={error} closable onClose={() => setError(null)} style={{ marginBottom: 16 }} />}
    {role === 'viewer' && <Alert type="info" message={t('businessData.readOnly')} style={{ marginBottom: 16 }} />}
    <Space wrap style={{ marginBottom: 16, width: '100%' }}>
      <Select aria-label={t('businessData.source')} value={selectedId} loading={loading} style={{ width: 320, maxWidth: '100%' }}
        placeholder={t('businessData.choose')} onChange={(id) => { setSelectedId(id); setSearchParams({ source: id }, { replace: true }); }}
        options={sources.map((source) => ({ value: source.id, label: `${source.name}${source.enabled ? '' : ` · ${t('common.disabled')}`}` }))} />
      <Button loading={loading} onClick={loadSources}>{t('businessData.refresh')}</Button>
    </Space>
    {loading && !sources.length ? <Spin /> : !selected ? <Empty description={t('businessData.empty')} /> : <>
      <Card size="small" style={{ marginBottom: 16 }} title={<Space wrap><Text strong>{selected.name}</Text><Tag>{t(`businessData.kinds.${selected.kind}`)}</Tag>
        <Tag color={selected.enabled ? 'green' : 'default'}>{t(selected.enabled ? 'common.enabled' : 'common.disabled')}</Tag></Space>}
        extra={<Space><Button size="small" disabled={!canConfigure || (selected.kind === 'table' && !recordsLoaded)} onClick={() => openSource(selected)}>{t('common.edit')}</Button>
          <Popconfirm title={t('businessData.deleteSource')} onConfirm={deleteSource} disabled={!canConfigure}>
            <Button size="small" danger disabled={!canConfigure}>{t('common.delete')}</Button>
          </Popconfirm></Space>}>
        {selected.description && <Typography.Paragraph>{selected.description}</Typography.Paragraph>}
        <Text>{t('businessData.bindHelp')} </Text><Link to="/agents">{t('nav.agents')}</Link>
        <div style={{ marginTop: 6 }}><Text copyable code>{selected.tool_name || selected.tool_id}</Text></div>
      </Card>
      {!selected.enabled && <Alert type="warning" message={t('businessData.disabledHelp')} style={{ marginBottom: 16 }} />}
      <Tabs activeKey={tab} onChange={setTab} items={[
        ...(selected.kind === 'table' ? [{ key: 'records', label: t('businessData.records'), children: <Card size="small">
          <Space wrap style={{ marginBottom: 12 }}>
            <Button icon={<PlusOutlined />} disabled={!canEdit} onClick={() => openRecord(null)}>{t('businessData.addRecord')}</Button>
            <Upload accept=".csv,text/csv" showUploadList={false} disabled={!canEdit || importing}
              beforeUpload={(file) => { void importCsv(file); return false; }}>
              <Button icon={<UploadOutlined />} loading={importing} disabled={!canEdit}>{t('businessData.importCsv')}</Button>
            </Upload>
            <Text type="secondary">{t('businessData.csvHelp')}</Text>
          </Space>
          <Table<BusinessRecord> size="small" rowKey="id" dataSource={records.items} loading={recordsLoading}
            scroll={{ x: 'max-content' }} locale={{ emptyText: t('businessData.noRecords') }}
            pagination={{ current: Math.floor(records.offset / PAGE_SIZE) + 1, pageSize: PAGE_SIZE, total: records.total,
              showSizeChanger: false, onChange: (page) => loadRecords(selected.id, (page - 1) * PAGE_SIZE) }}
            columns={[...dataColumns, { title: t('common.actions'), key: 'actions', width: 140, fixed: 'right', render: (_, record) => <Space>
              <Button size="small" disabled={!canEdit} onClick={() => openRecord(record)}>{t('common.edit')}</Button>
              <Popconfirm title={t('businessData.deleteRecord')} onConfirm={() => deleteRecord(record)} disabled={!canEdit}>
                <Button size="small" danger disabled={!canEdit}>{t('common.delete')}</Button>
              </Popconfirm></Space> }]} />
        </Card> }] : []),
        { key: 'query', label: t('businessData.query'), children: <Card size="small">
          <Typography.Paragraph type="secondary">{t('businessData.queryHelp', { count: selected.max_rows })}</Typography.Paragraph>
          <Form name="business-query" form={queryForm} layout="vertical" onFinish={runQuery}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
              {selected.fields.filter((field) => selected.filter_fields.includes(field.name)).map((field) =>
                <Form.Item key={field.name} name={field.name} label={field.name} rules={[{ required: selected.required_filters.includes(field.name) }]}>{fieldInput(field)}</Form.Item>)}
            </div>
            <Button type="primary" htmlType="submit" loading={queryLoading} disabled={!canEdit || !selected.enabled}>{t('businessData.runQuery')}</Button>
          </Form>
          {queryResult && <div style={{ marginTop: 16 }}>
            <Text>{t('businessData.rowCount', { count: queryResult.row_count })}</Text>
            {queryResult.truncated && <Alert type="info" message={t('businessData.truncated')} style={{ marginTop: 8 }} />}
            <Table size="small" rowKey={(_, index) => String(index)} pagination={false} scroll={{ x: 'max-content' }}
              dataSource={queryResult.rows}
              columns={queryResult.columns.map((field) => ({ title: field.name, dataIndex: field.name, key: field.name, width: 160, render: formatValue }))} />
          </div>}
        </Card> },
        { key: 'schema', label: t('businessData.schema'), children: <Card size="small">
          {selected.kind === 'postgres' && <Typography.Paragraph>
            <Text code>{selected.postgres_schema}.{selected.postgres_table}</Text> · {t(selected.has_credentials ? 'businessData.credentialsSaved' : 'businessData.credentialsMissing')}
            {selected.tenant_column && <> · {t('businessData.tenantColumn')}: <Text code>{selected.tenant_column}</Text></>}
          </Typography.Paragraph>}
          <Table size="small" rowKey="name" pagination={false} dataSource={selected.fields}
            columns={[{ title: t('businessData.fieldName'), dataIndex: 'name' }, { title: t('businessData.fieldType'), dataIndex: 'type' },
              { title: t('businessData.required'), dataIndex: 'required', render: (value) => t(value ? 'businessData.true' : 'businessData.false') },
              { title: t('businessData.filters'), key: 'filter', render: (_, field) => selected.required_filters.includes(field.name)
                ? t('businessData.requiredFilter') : selected.filter_fields.includes(field.name) ? t('businessData.optionalFilter') : '—' }]} />
        </Card> },
      ]} />
    </>}
    <Modal open={sourceOpen} title={t(editingSource ? 'businessData.edit' : 'businessData.create')} width={760}
      style={{ maxWidth: 'calc(100vw - 32px)' }} styles={{ body: { maxHeight: '75vh', overflowY: 'auto' } }}
      confirmLoading={saving} onOk={saveSource} onCancel={() => { setSourceOpen(false); sourceForm.resetFields(); }}>
      {sourceError && <Alert type="error" message={sourceError} style={{ marginBottom: 12 }} />}
      <Form name="business-source" form={sourceForm} layout="vertical">
        <Form.Item name="name" label={t('businessData.name')} rules={[{ required: true, whitespace: true }]}><Input maxLength={128} /></Form.Item>
        <Form.Item name="description" label={t('businessData.sourceDescription')}><Input.TextArea rows={2} /></Form.Item>
        <Form.Item name="kind" label={t('businessData.kind')}><Select disabled={!!editingSource} options={[
          { value: 'table', label: t('businessData.kinds.table') }, { value: 'postgres', label: t('businessData.kinds.postgres'), disabled: role !== 'admin' },
        ]} /></Form.Item>
        <Text strong>{t('businessData.fields')}</Text>
        {schemaLocked && <Alert type="info" message={t('businessData.schemaLocked')} style={{ margin: '8px 0' }} />}
        <Form.List name="fields" rules={[{ validator: async (_, value) => {
          if (!value?.length) throw new Error(t('businessData.oneField'));
          if (new Set(value.map((field: BusinessField) => field.name)).size !== value.length) throw new Error(t('businessData.uniqueFields'));
        } }]}>{(items, { add, remove }, { errors }) => <>
          {items.map((item) => <div key={item.key} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'start', marginTop: 8 }}>
            <Form.Item name={[item.name, 'name']} rules={[{ required: true, pattern: IDENTIFIER, message: t('businessData.identifierHelp') }]} style={{ flex: '1 1 180px', marginBottom: 8 }}>
              <Input aria-label={`${t('businessData.fieldName')} ${item.name + 1}`} placeholder={t('businessData.fieldName')} disabled={schemaLocked} />
            </Form.Item>
            <Form.Item name={[item.name, 'type']} rules={[{ required: true }]} style={{ width: 125, marginBottom: 8 }}>
              <Select aria-label={`${t('businessData.fieldType')} ${item.name + 1}`} disabled={schemaLocked}
                options={['string', 'integer', 'number', 'boolean', 'date'].map((value) => ({ value, label: value }))} />
            </Form.Item>
            <Form.Item name={[item.name, 'required']} valuePropName="checked" style={{ marginBottom: 8 }}><Checkbox disabled={schemaLocked}>{t('businessData.required')}</Checkbox></Form.Item>
            <Button aria-label={t('businessData.removeField')} icon={<DeleteOutlined />} disabled={schemaLocked} onClick={() => remove(item.name)} />
          </div>)}
          <Form.ErrorList errors={errors} />
          <Button type="dashed" icon={<PlusOutlined />} disabled={schemaLocked} onClick={() => add({ name: '', type: 'string', required: false })}>{t('businessData.addField')}</Button>
        </>}</Form.List>
        <Form.Item name="filter_fields" label={t('businessData.filters')} style={{ marginTop: 16 }}><Select mode="multiple" options={fieldOptions} /></Form.Item>
        <Form.Item name="required_filters" label={t('businessData.requiredFilters')} extra={t('businessData.requiredFiltersHelp')}><Select mode="multiple" options={fieldOptions.filter((option: { value: string }) => filterFields.includes(option.value))} /></Form.Item>
        <Form.Item name="max_rows" label={t('businessData.maxRows')} rules={[{ required: true }]}><InputNumber min={1} max={200} /></Form.Item>
        {kind === 'postgres' && <>
          <Alert type="info" message={t('businessData.pgHelp')} style={{ marginBottom: 16 }} />
          <Form.Item name="postgres_dsn" label={t('businessData.dsn')} rules={[{ required: !editingSource?.has_credentials }]} extra={editingSource?.has_credentials ? t('businessData.keepCredentials') : undefined}>
            <Input.Password autoComplete="new-password" placeholder="postgresql://user:password@host:5432/database" />
          </Form.Item>
          <Form.Item name="postgres_schema" label={t('businessData.pgSchema')} rules={[{ required: true, pattern: IDENTIFIER }]}><Input /></Form.Item>
          <Form.Item name="postgres_table" label={t('businessData.pgTable')} rules={[{ required: true, pattern: IDENTIFIER }]}><Input /></Form.Item>
          <Form.Item name="tenant_column" label={t('businessData.tenantColumn')} rules={[{ pattern: IDENTIFIER }]} extra={t('businessData.tenantHelp')}><Input /></Form.Item>
        </>}
        <Form.Item name="enabled" label={t('common.enabled')} valuePropName="checked"><Switch /></Form.Item>
      </Form>
    </Modal>
    <Modal open={recordOpen} title={t(editingRecord ? 'businessData.editRecord' : 'businessData.addRecord')}
      onOk={saveRecord} confirmLoading={saving} onCancel={() => setRecordOpen(false)}>
      {recordError && <Alert type="error" message={recordError} style={{ marginBottom: 12 }} />}
      <Form name="business-record" form={recordForm} layout="vertical">
        {selected?.fields.map((field) => <Form.Item key={field.name} name={field.name} label={field.name} rules={[{ required: field.required }]}>{fieldInput(field)}</Form.Item>)}
      </Form>
    </Modal>
  </div>;
}
