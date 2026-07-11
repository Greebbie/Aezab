import { useTranslation } from 'react-i18next';
import { Button, Result, Typography } from 'antd';

interface CompleteStepProps {
  agentName: string | null;
  onGoToPlayground: () => void;
  onFinish: () => void;
}

export default function CompleteStep({ agentName, onGoToPlayground, onFinish }: CompleteStepProps) {
  const { t } = useTranslation();

  return (
    <div>
      <Result
        status="success"
        title={agentName ? t('setup.step3.title', { name: agentName }) : t('setup.step3.titleGeneric')}
        extra={[
          <Button type="primary" key="playground" onClick={onGoToPlayground}>
            {t('setup.step3.goToPlayground')}
          </Button>,
          <Button key="finish" onClick={onFinish}>
            {t('common.finish')}
          </Button>,
        ]}
      />
      <Typography.Paragraph type="secondary" style={{ textAlign: 'center' }}>
        {t('setup.step3.knowledgeHint')}
      </Typography.Paragraph>
    </div>
  );
}
