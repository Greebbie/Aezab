import { Button, message } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import type { IntegrationCopy } from './copy';

interface Props {
  value: string;
  copy: IntegrationCopy;
}

export default function IntegrationCodeBlock({ value, copy }: Props) {
  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(value);
      message.success(copy.actions.copied);
    } catch {
      message.error(copy.actions.copyFailed);
    }
  };

  return (
    <div
      style={{
        border: '1px solid #d9d9d9',
        borderRadius: 8,
        background: '#111827',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <Button
        size="small"
        icon={<CopyOutlined />}
        onClick={copyText}
        style={{ position: 'absolute', right: 8, top: 8, zIndex: 1 }}
      >
        {copy.actions.copy}
      </Button>
      <pre
        style={{
          color: '#e5e7eb',
          margin: 0,
          padding: '40px 16px 16px',
          overflow: 'auto',
          fontSize: 12,
          lineHeight: 1.6,
          maxHeight: 360,
        }}
      >
        {value}
      </pre>
    </div>
  );
}
