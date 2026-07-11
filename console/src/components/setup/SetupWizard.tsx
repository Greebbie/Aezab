import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Modal, Steps } from 'antd';
import type { Agent } from '../../types';
import ProviderStep from './ProviderStep';
import AgentTemplateStep from './AgentTemplateStep';
import CompleteStep from './CompleteStep';

export const SETUP_DONE_STORAGE_KEY = 'aezab_setup_done';
export const OPEN_SETUP_WIZARD_EVENT = 'aezab:open-setup-wizard';

interface SetupWizardProps {
  open: boolean;
  onClose: () => void;
}

export default function SetupWizard({ open, onClose }: SetupWizardProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [step, setStep] = useState(0);
  const [llmConfigId, setLlmConfigId] = useState<string | null>(null);
  const [createdAgent, setCreatedAgent] = useState<Agent | null>(null);

  useEffect(() => {
    if (!open) return;
    setStep(0);
    setLlmConfigId(null);
    setCreatedAgent(null);
  }, [open]);

  const markDone = () => {
    localStorage.setItem(SETUP_DONE_STORAGE_KEY, 'true');
  };

  const handleClose = () => {
    markDone();
    onClose();
  };

  const handleAgentCreated = (agent: Agent) => {
    setCreatedAgent(agent);
    setStep(2);
  };

  const handleSkipAgent = () => {
    markDone();
    onClose();
  };

  const handleGoToPlayground = () => {
    markDone();
    onClose();
    navigate('/playground');
  };

  const stepItems = [
    { title: t('setup.stepConnectModel') },
    { title: t('setup.stepCreateAgent') },
    { title: t('setup.stepDone') },
  ];

  return (
    <Modal
      title={t('setup.modalTitle')}
      open={open}
      onCancel={handleClose}
      width={760}
      footer={null}
      closable
      maskClosable={false}
      destroyOnClose
    >
      <Steps current={step} size="small" items={stepItems} style={{ marginBottom: 24 }} />

      {step === 0 && (
        <ProviderStep
          onConfigCreated={setLlmConfigId}
          onNext={() => setStep(1)}
        />
      )}

      {step === 1 && (
        <AgentTemplateStep
          llmConfigId={llmConfigId}
          onAgentCreated={handleAgentCreated}
          onSkip={handleSkipAgent}
        />
      )}

      {step === 2 && (
        <CompleteStep
          agentName={createdAgent?.name ?? null}
          onGoToPlayground={handleGoToPlayground}
          onFinish={handleClose}
        />
      )}
    </Modal>
  );
}
