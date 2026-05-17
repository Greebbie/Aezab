import React, { useState, useEffect } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import './i18n';
import i18n from './i18n';
import App from './App';
import './styles.css';

function Root() {
  const [locale, setLocale] = useState(i18n.language === 'en' ? enUS : zhCN);

  useEffect(() => {
    const handleLangChange = (lng: string) => {
      setLocale(lng === 'en' ? enUS : zhCN);
    };
    i18n.on('languageChanged', handleLangChange);
    return () => { i18n.off('languageChanged', handleLangChange); };
  }, []);

  return (
    <ConfigProvider
      locale={locale}
      theme={{
        token: {
          colorPrimary: '#2563eb',
          colorInfo: '#2563eb',
          colorText: '#182033',
          colorTextSecondary: '#64748b',
          colorBgLayout: '#f4f6f9',
          colorBorder: '#e2e8f0',
          borderRadius: 8,
          fontSize: 14,
        },
        components: {
          Layout: {
            siderBg: '#111827',
            triggerBg: '#0f172a',
          },
          Menu: {
            darkItemBg: '#111827',
            darkSubMenuItemBg: '#111827',
            darkItemSelectedBg: '#1d4ed8',
            darkItemHoverBg: '#1f2937',
          },
          Card: {
            borderRadiusLG: 8,
            headerFontSize: 14,
          },
          Table: {
            headerBg: '#f8fafc',
            headerColor: '#334155',
            rowHoverBg: '#f8fafc',
          },
        },
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
