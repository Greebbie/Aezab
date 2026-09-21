import { Alert, Button, Form, Input, InputNumber, Select, Space } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { WorkflowStep } from '../../types';

export default function DecisionEditor({ steps }: { steps: WorkflowStep[] }) {
  const { t } = useTranslation();
  const fields = Array.from(new Set(steps.flatMap((step) => [
    ...(step.fields || []).map((field) => field.name),
    ...Object.keys((step.tool_config?.output_mapping || {}) as Record<string, unknown>),
  ])));
  return <>
    <Alert type="info" showIcon message={t('workflows.decision.explanation')} style={{ marginBottom: 16 }} />
    <Form.Item name={['tool_config', 'provider']} initialValue="typesafe" hidden><Input /></Form.Item>
    <Form.Item name={['tool_config', 'instructions']} label={t('workflows.decision.instructions')} rules={[{ required: true }]}>
      <Input.TextArea rows={3} maxLength={4000} placeholder={t('workflows.decision.instructionsPlaceholder')} />
    </Form.Item>
    <Form.Item name={['tool_config', 'input_fields']} label={t('workflows.decision.inputs')} rules={[{ required: true }]} extra={t('workflows.decision.inputsHelp')}>
      <Select mode="multiple" options={fields.map((value) => ({ value, label: value }))} />
    </Form.Item>
    <Form.Item name={['tool_config', 'result_key']} label={t('workflows.decision.resultKey')} rules={[{ required: true }, { pattern: /^[A-Za-z][A-Za-z0-9_]*$/, message: t('workflows.decision.keyHelp') }]}>
      <Input placeholder="route" />
    </Form.Item>
    <Form.List name={['tool_config', 'choices']}>
      {(choices, { add, remove }) => <>
        {choices.map(({ key, name }) => <Space key={key} align="start" style={{ display: 'flex' }} wrap>
          <Form.Item name={[name, 'value']} label={t('workflows.decision.choice')} rules={[{ required: true }]}><Input placeholder="refund" /></Form.Item>
          <Form.Item name={[name, 'description']} label={t('common.description')} rules={[{ required: true }]}><Input style={{ width: 280, maxWidth: '100%' }} /></Form.Item>
          <Button aria-label={t('common.delete')} danger icon={<DeleteOutlined />} onClick={() => remove(name)} style={{ marginTop: 30 }} />
        </Space>)}
        <Button icon={<PlusOutlined />} onClick={() => add({ value: '', description: '' })}>{t('workflows.decision.addChoice')}</Button>
      </>}
    </Form.List>
    <Space wrap style={{ marginTop: 16 }}>
      <Form.Item name={['tool_config', 'min_confidence']} label={t('workflows.decision.confidence')} initialValue={0.8} rules={[{ required: true }]}><InputNumber min={0} max={1} step={0.05} /></Form.Item>
      <Form.Item name={['tool_config', 'min_probability']} label={t('workflows.decision.probability')} initialValue={0.8} rules={[{ required: true }]}><InputNumber min={0} max={1} step={0.05} /></Form.Item>
    </Space>
    <Alert type="warning" showIcon message={t('workflows.decision.safeFallback')} />
  </>;
}
