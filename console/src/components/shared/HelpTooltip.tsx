import type { ReactNode } from 'react';
import { Tooltip } from 'antd';
import { QuestionCircleOutlined } from '@ant-design/icons';

interface HelpTooltipProps {
  content: ReactNode;
}

interface HelpLabelProps {
  label: ReactNode;
  help: ReactNode;
}

export default function HelpTooltip({ content }: HelpTooltipProps) {
  return (
    <Tooltip
      title={<div className="aezab-help-tooltip">{content}</div>}
      placement="top"
      mouseEnterDelay={0.2}
      overlayInnerStyle={{ maxWidth: 360 }}
    >
      <span className="aezab-help-icon" aria-label="Help" role="img">
        <QuestionCircleOutlined />
      </span>
    </Tooltip>
  );
}

export function HelpLabel({ label, help }: HelpLabelProps) {
  return (
    <span className="aezab-help-label">
      <span>{label}</span>
      <HelpTooltip content={help} />
    </span>
  );
}
