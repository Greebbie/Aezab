import { useState } from 'react';
import { Card, Form, Input, Button, Alert, Typography } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { authApi, setToken } from '../api';

const { Title, Text } = Typography;

interface LoginFormValues {
  username: string;
  password: string;
  confirmPassword?: string;
}

interface LoginPageProps {
  mode: 'login' | 'setup';
  onSuccess: () => void;
}

function extractErrorMessage(err: unknown, badCredentialsText: string, genericText: string): string {
  if (err instanceof Error) {
    if (err.message.toLowerCase().includes('invalid username or password')) {
      return badCredentialsText;
    }
    return err.message || genericText;
  }
  return genericText;
}

export default function LoginPage({ mode, onSuccess }: LoginPageProps) {
  const { t } = useTranslation();
  const [form] = Form.useForm<LoginFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (values: LoginFormValues) => {
    setError(null);

    if (mode === 'setup' && values.password !== values.confirmPassword) {
      setError(t('auth.errorPasswordMismatch'));
      return;
    }
    if (mode === 'setup' && values.password.length < 8) {
      setError(t('auth.errorPasswordTooShort'));
      return;
    }

    setSubmitting(true);
    try {
      if (mode === 'setup') {
        await authApi.register({ username: values.username, password: values.password });
      }
      const res = await authApi.login(values.username, values.password);
      setToken(res.data.access_token);
      onSuccess();
    } catch (err: unknown) {
      setError(extractErrorMessage(err, t('auth.errorBadCredentials'), t('auth.errorGeneric')));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f4f6f9',
      }}
    >
      <Card style={{ width: 380 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: 8,
              background: '#2563eb',
              color: '#fff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 22,
              fontWeight: 700,
              margin: '0 auto 12px',
            }}
          >
            A
          </div>
          <Title level={4} style={{ marginBottom: 4 }}>
            {mode === 'setup' ? t('auth.setupTitle') : t('auth.title')}
          </Title>
          {mode === 'setup' && (
            <Text type="secondary">{t('auth.setupSubtitle')}</Text>
          )}
        </div>

        {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}

        <Form form={form} layout="vertical" onFinish={handleSubmit} disabled={submitting}>
          <Form.Item
            name="username"
            label={t('auth.username')}
            rules={[{ required: true, message: t('common.required') }]}
          >
            <Input prefix={<UserOutlined />} autoComplete="username" autoFocus />
          </Form.Item>
          <Form.Item
            name="password"
            label={t('auth.password')}
            rules={[{ required: true, message: t('common.required') }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              autoComplete={mode === 'setup' ? 'new-password' : 'current-password'}
            />
          </Form.Item>
          {mode === 'setup' && (
            <Form.Item
              name="confirmPassword"
              label={t('auth.confirmPassword')}
              rules={[{ required: true, message: t('common.required') }]}
            >
              <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
            </Form.Item>
          )}
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              {mode === 'setup' ? t('auth.createAdmin') : t('auth.login')}
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
