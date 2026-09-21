import React from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Tag } from 'antd';
import {
  FormOutlined,
  CheckCircleOutlined,
  ApiOutlined,
  QuestionCircleOutlined,
  UserOutlined,
  FlagOutlined,
} from '@ant-design/icons';
import type { WorkflowStep, StepType } from '../../types';
import { useTranslation } from 'react-i18next';
import { workflowRules } from './graph';

const STEP_CONFIG: Record<StepType, { icon: React.ReactNode; color: string; label: string }> = {
  collect: { icon: <FormOutlined />, color: '#1890ff', label: 'Collect' },
  validate: { icon: <CheckCircleOutlined />, color: '#52c41a', label: 'Validate' },
  tool_call: { icon: <ApiOutlined />, color: '#722ed1', label: 'Tool Call' },
  decision: { icon: <QuestionCircleOutlined />, color: '#ad6800', label: 'Decision' },
  confirm: { icon: <QuestionCircleOutlined />, color: '#fa8c16', label: 'Confirm' },
  human_review: { icon: <UserOutlined />, color: '#eb2f96', label: 'Review' },
  complete: { icon: <FlagOutlined />, color: '#13c2c2', label: 'Complete' },
};

interface WorkflowNodeData {
  step: WorkflowStep;
  index: number;
  total: number;
}

export default function WorkflowNode({ data, selected }: NodeProps<WorkflowNodeData>) {
  const { t } = useTranslation();
  const { step, index, total } = data;
  const config = STEP_CONFIG[step.step_type as StepType] || STEP_CONFIG.collect;
  const fieldCount = Array.isArray(step.fields) ? step.fields.length : 0;
  const hasRules = workflowRules(step.next_step_rules).length > 0;

  return (
    <div
      style={{
        padding: '12px 16px',
        borderRadius: 8,
        border: `2px solid ${selected ? config.color : '#cbd5e1'}`,
        background: selected ? `${config.color}10` : '#ffffff',
        minWidth: 200,
        cursor: 'pointer',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: '#555' }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ color: config.color, fontSize: 16 }}>{config.icon}</span>
        <strong style={{ color: '#172b4d', fontSize: 13 }}>{step.name}</strong>
        <Tag color={config.color} style={{ marginLeft: 'auto', fontSize: 10 }}>
          {t(`workflows.stepTypes.${step.step_type}`)}
        </Tag>
      </div>
      <div style={{ fontSize: 12, color: '#52647a' }}>
        {index + 1}/{total}
        {fieldCount > 0 && <span> · {fieldCount} {t('workflows.fields')}</span>}
        {hasRules && <span> · {t('workflows.branchingRules')}</span>}
      </div>
      {step.prompt_template && (
        <div
          style={{
            fontSize: 10,
            color: '#666',
            marginTop: 4,
            maxWidth: 200,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {step.prompt_template.slice(0, 60)}
        </div>
      )}
      {step.step_type !== 'complete' && <Handle id="success" type="source" position={Position.Bottom} style={{ background: '#1677ff', width: 11, height: 11 }} title={t('workflows.graph.default')} />}
      {['tool_call', 'decision', 'validate'].includes(step.step_type) && <Handle id="failure" type="source" position={Position.Right} style={{ background: '#cf1322', width: 11, height: 11 }} title={t('workflows.graph.failure')} />}
    </div>
  );
}
