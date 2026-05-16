import { Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button } from 'antd';
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

const { Header, Sider, Content } = Layout;

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const { t, i18n } = useTranslation();

  const toggleLang = () => {
    const next = i18n.language === 'zh' ? 'en' : 'zh';
    i18n.changeLanguage(next);
    localStorage.setItem('hlab-lang', next);
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

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="dark" width={200}>
        <div style={{ height: 48, margin: 16, color: '#fff', fontSize: 18, fontWeight: 'bold', textAlign: 'center', lineHeight: '48px' }}>
          HlAB Console
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px', fontSize: 16, fontWeight: 500, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span>Headless AI Agent Builder</span>
          <Button
            type="text"
            icon={<GlobalOutlined />}
            onClick={toggleLang}
          >
            {i18n.language === 'zh' ? 'EN' : '中文'}
          </Button>
        </Header>
        <Content style={{ margin: 24, padding: 24, background: '#fff', borderRadius: 8 }}>
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
        </Content>
      </Layout>
    </Layout>
  );
}
