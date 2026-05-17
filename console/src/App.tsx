import { Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, Space, Tag } from 'antd';
import { useTranslation } from 'react-i18next';
import {
  RobotOutlined,
  ApartmentOutlined,
  BookOutlined,
  ApiOutlined,
  AuditOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  AppstoreOutlined,
  GlobalOutlined,
  HeartOutlined,
  LinkOutlined,
} from '@ant-design/icons';

import AgentsPage from './pages/AgentsPage';
import WorkflowsPage from './pages/WorkflowsPage';
import KnowledgePage from './pages/KnowledgePage';
import ToolsPage from './pages/ToolsPage';
import AuditPage from './pages/AuditPage';
import DashboardPage from './pages/DashboardPage';
import PlaygroundPage from './pages/PlaygroundPage';
import LLMConfigsPage from './pages/LLMConfigsPage';
import SettingsPage from './pages/SettingsPage';
import SkillsPage from './pages/SkillsPage';
import HealthPage from './pages/HealthPage';
import IntegrationsPage from './pages/IntegrationsPage';
import { LANGUAGE_STORAGE_KEY } from './i18n';

const { Header, Sider, Content } = Layout;

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const { t, i18n } = useTranslation();

  const toggleLang = () => {
    const next = i18n.language === 'zh' ? 'en' : 'zh';
    i18n.changeLanguage(next);
    localStorage.setItem(LANGUAGE_STORAGE_KEY, next);
  };

  const menuItems = [
    { key: '/', icon: <DashboardOutlined />, label: t('nav.dashboard') },
    { key: '/playground', icon: <ExperimentOutlined />, label: t('nav.playground') },
    { key: '/integrations', icon: <LinkOutlined />, label: t('nav.integrations') },
    { key: '/agents', icon: <RobotOutlined />, label: t('nav.agents') },
    { key: '/skills', icon: <AppstoreOutlined />, label: t('nav.skills') },
    { key: '/workflows', icon: <ApartmentOutlined />, label: t('nav.workflows') },
    { key: '/knowledge', icon: <BookOutlined />, label: t('nav.knowledge') },
    { key: '/tools', icon: <ApiOutlined />, label: t('nav.tools') },
    { key: '/llm-configs', icon: <ThunderboltOutlined />, label: t('nav.llmConfigs') },
    { key: '/audit', icon: <AuditOutlined />, label: t('nav.audit') },
    { key: '/settings', icon: <SettingOutlined />, label: t('nav.settings') },
    { key: '/health', icon: <HeartOutlined />, label: t('nav.health') },
  ];

  const currentPath = menuItems.some((item) => item.key === location.pathname)
    ? location.pathname
    : '/';
  const currentItem = menuItems.find((item) => item.key === currentPath);

  return (
    <Layout className="aezab-shell">
      <Sider theme="dark" width={216} className="aezab-sidebar">
        <div className="aezab-brand">
          <div className="aezab-brand-mark">A</div>
          <div>
            <div className="aezab-brand-title">Aezab</div>
            <div className="aezab-brand-subtitle">{t('shell.brandSubtitle')}</div>
          </div>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[currentPath]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header className="aezab-topbar">
          <div>
            <div className="aezab-topbar-title">{currentItem?.label || t('shell.console')}</div>
            <div className="aezab-topbar-subtitle">{t('shell.topbarSubtitle')}</div>
          </div>
          <Space size={12}>
            <Tag color="success">{t('shell.localReady')}</Tag>
            <Button type="text" icon={<GlobalOutlined />} onClick={toggleLang}>
              {i18n.language === 'zh' ? 'EN' : '中文'}
            </Button>
          </Space>
        </Header>
        <Content className="aezab-content">
          <div className="aezab-page">
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/playground" element={<PlaygroundPage />} />
              <Route path="/integrations" element={<IntegrationsPage />} />
              <Route path="/agents" element={<AgentsPage />} />
              <Route path="/workflows" element={<WorkflowsPage />} />
              <Route path="/knowledge" element={<KnowledgePage />} />
              <Route path="/skills" element={<SkillsPage />} />
              <Route path="/tools" element={<ToolsPage />} />
              <Route path="/llm-configs" element={<LLMConfigsPage />} />
              <Route path="/audit" element={<AuditPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/health" element={<HealthPage />} />
            </Routes>
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
