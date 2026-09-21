import { Alert, Button, Form, Input, InputNumber, Select, Space, Typography, type FormInstance } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { WorkflowStep } from '../../types';

const operators = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'not_contains', 'in', 'not_in', 'regex'];

export default function RouteEditor({ form, steps, stepId }: {
  form: FormInstance; steps: WorkflowStep[]; stepId?: string;
}) {
  const { t } = useTranslation();
  const rules = Form.useWatch('branch_rules', form) || [];
  const draftFields = Form.useWatch('fields', form) || [];
  const draftChoices = Form.useWatch(['tool_config', 'choices'], form) || [];
  const fieldNames = Array.from(new Set(steps.flatMap((step) => [
    ...(step.fields || []).map((field) => field.name),
    ...(step.step_type === 'decision' && step.tool_config?.result_key ? [String(step.tool_config.result_key)] : []),
    ...Object.keys((step.tool_config?.output_mapping || {}) as Record<string, unknown>),
  ])));
  const resultKey = Form.useWatch(['tool_config', 'result_key'], form);
  draftFields.forEach((field: { name?: string }) => { if (field.name && !fieldNames.includes(field.name)) fieldNames.push(field.name); });
  if (resultKey && !fieldNames.includes(resultKey)) fieldNames.push(resultKey);
  const options = steps.filter((step) => step.id !== stepId).map((step) => ({ value: step.id, label: `${step.order} · ${step.name}` }));

  return <>
    <Alert type="info" showIcon message={t('workflows.graph.ruleOrder')} style={{ marginBottom: 12 }} />
    <Form.List name="branch_rules">
      {(fields, { add, remove, move }) => <>
        {fields.map(({ key, name }) => {
          const rule = rules[name];
          const isDefault = rule?.condition == null;
          const op = rule?.condition?.op || 'eq';
          const decisionStep = steps.find((step) => step.step_type === 'decision' && step.tool_config?.result_key === rule?.condition?.field);
          const choices = rule?.condition?.field === resultKey ? draftChoices : decisionStep?.tool_config?.choices;
          const choiceOptions = Array.isArray(choices) ? choices.filter((choice) => choice.value && choice.value !== 'other').map((choice) => ({ value: choice.value, label: choice.value })) : [];
          return <div key={key} style={{ borderBottom: '1px solid #e8edf2', padding: '12px 0' }}>
            <Space style={{ marginBottom: 8 }} wrap>
              <Typography.Text strong>{isDefault ? t('workflows.graph.default') : t('workflows.graph.conditionNumber', { n: name + 1 })}</Typography.Text>
              {!isDefault && name > 0 && <Button size="small" onClick={() => move(name, name - 1)}>{t('workflows.graph.moveUp')}</Button>}
              <Button size="small" danger icon={<DeleteOutlined />} onClick={() => remove(name)}>{t('workflows.graph.disconnect')}</Button>
            </Space>
            {!isDefault && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Form.Item name={[name, 'condition', 'field']} label={t('workflows.graph.field')} rules={[{ required: true }]} style={{ flex: 2, minWidth: 150 }}>
                <Select showSearch options={fieldNames.map((value) => ({ value, label: value }))} />
              </Form.Item>
              <Form.Item name={[name, 'condition', 'op']} label={t('workflows.graph.operator')} style={{ flex: 1, minWidth: 140 }}>
                <Select options={operators.map((value) => ({ value, label: t(`workflows.graph.operators.${value}`) }))}
                  onChange={() => form.setFieldValue(['branch_rules', name, 'condition', 'value'], undefined)} />
              </Form.Item>
              <Form.Item name={[name, 'condition', 'value']} label={t('workflows.graph.value')} rules={[{ required: true }]} style={{ flex: 2, minWidth: 150 }}>
                {['gt', 'gte', 'lt', 'lte'].includes(op) ? <InputNumber style={{ width: '100%' }} />
                  : ['in', 'not_in'].includes(op) ? <Select mode="tags" tokenSeparators={[',']} options={choiceOptions} />
                    : choiceOptions.length > 0 && ['eq', 'ne'].includes(op) ? <Select options={choiceOptions} /> : <Input />}
              </Form.Item>
            </div>}
            <Form.Item name={[name, 'goto_step']} label={t('workflows.graph.target')} rules={[{ required: true }]}>
              <Select options={options} />
            </Form.Item>
          </div>;
        })}
        <Space wrap style={{ marginTop: 12 }}>
          <Button icon={<PlusOutlined />} onClick={() => {
            const defaultIndex = rules.findIndex((rule: { condition: unknown }) => rule.condition == null);
            add({ condition: { field: '', op: 'eq', value: '' }, goto_step: undefined }, defaultIndex < 0 ? fields.length : defaultIndex);
          }}>{t('workflows.graph.addCondition')}</Button>
          {!rules.some((rule: { condition: unknown }) => rule.condition == null) &&
            <Button onClick={() => add({ condition: null, goto_step: undefined })}>{t('workflows.graph.addDefault')}</Button>}
        </Space>
      </>}
    </Form.List>
  </>;
}
