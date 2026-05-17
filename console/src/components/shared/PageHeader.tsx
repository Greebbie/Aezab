import type { ReactNode } from 'react';
import { Space, Tag } from 'antd';

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  eyebrow?: ReactNode;
  status?: ReactNode;
  actions?: ReactNode;
}

export default function PageHeader({
  title,
  description,
  eyebrow,
  status,
  actions,
}: PageHeaderProps) {
  return (
    <div className="aezab-page-header">
      <div>
        {eyebrow ? <div className="aezab-page-eyebrow">{eyebrow}</div> : null}
        <Space align="center" size={10}>
          <h1 className="aezab-page-title">{title}</h1>
          {status ? <Tag color="blue">{status}</Tag> : null}
        </Space>
        {description ? <div className="aezab-page-description">{description}</div> : null}
      </div>
      {actions ? <Space wrap>{actions}</Space> : null}
    </div>
  );
}
