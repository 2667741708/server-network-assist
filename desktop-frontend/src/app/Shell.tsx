import { useState, type ReactNode } from 'react';
import {
  ArrowClockwise24Regular,
  ArrowSync24Regular,
  Globe24Regular,
  Home24Regular,
  Navigation24Regular,
  People24Regular,
  Settings24Regular,
  Share24Regular,
  ShieldTask24Regular,
} from '@fluentui/react-icons';
import { Button, Select, Tooltip } from '@fluentui/react-components';
import type { StatusResponse } from '../api/types';
import type { NavigationItem, SectionId, ThemeMode } from './types';
import { StatusPill } from './ui';

const iconMap = {
  overview: <Home24Regular />,
  'tunnel-page': <ArrowSync24Regular />,
  sharing: <Share24Regular />,
  'hosts-page': <People24Regular />,
  'proxy-page': <Globe24Regular />,
  diagnostics: <ShieldTask24Regular />,
  settings: <Settings24Regular />,
};

export const navigationItems: NavigationItem[] = [
  {id: 'overview', label: '连接概览', description: '本机网络总览', icon: iconMap.overview},
  {id: 'tunnel-page', label: '本机隧道', description: 'WireGuard 与物理网络', icon: iconMap['tunnel-page']},
  {id: 'sharing', label: '共享网络', description: '远端方案与恢复', icon: iconMap.sharing},
  {id: 'hosts-page', label: '主机与凭据', description: '主机身份与登录凭据', icon: iconMap['hosts-page']},
  {id: 'proxy-page', label: '系统代理', description: '当前用户代理与备份', icon: iconMap['proxy-page']},
  {id: 'diagnostics', label: '诊断与恢复', description: '问题定位与安全恢复', icon: iconMap.diagnostics},
  {id: 'settings', label: '后台与更新', description: '运行状态与版本', icon: iconMap.settings},
];

const pageTitles: Record<SectionId, string> = {
  overview: '连接概览',
  'tunnel-page': '本机隧道',
  sharing: '共享网络',
  'hosts-page': '主机与凭据',
  'proxy-page': '系统代理',
  diagnostics: '诊断与恢复',
  settings: '后台与更新',
};

function networkLevel(status: StatusResponse | null): 'ok' | 'warning' | 'danger' | 'info' {
  if (!status) return 'info';
  const direct = status.direct?.ok;
  const system = status.system?.ok;
  if (direct === false && system === false) return 'danger';
  if (direct === false || system === false) return 'warning';
  if (direct == null || system == null) return 'info';
  return 'ok';
}

function networkLabel(status: StatusResponse | null): string {
  const level = networkLevel(status);
  return level === 'ok' ? '网络状态正常' : level === 'danger' ? '公网不可达' : level === 'warning' ? '存在网络异常' : '状态待确认';
}

export interface ShellProps {
  currentSection: SectionId;
  onNavigate: (section: SectionId) => void;
  status: StatusResponse | null;
  themeMode: ThemeMode;
  onThemeModeChange: (mode: ThemeMode) => void;
  onRefresh: () => void;
  busy: boolean;
  children: ReactNode;
}

export function Shell({currentSection, onNavigate, status, themeMode, onThemeModeChange, onRefresh, busy, children}: ShellProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const level = networkLevel(status);

  const navigate = (section: SectionId) => {
    onNavigate(section);
    setMobileOpen(false);
  };

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">跳转到主要内容</a>
    {mobileOpen && <button className="mobile-overlay" type="button" aria-label="关闭导航" onClick={() => setMobileOpen(false)} />}
    <aside id="navigation" className={`sidebar ${mobileOpen ? 'mobile-open' : ''}`} aria-label="主导航">
      <div className="sidebar-brand">
        <div className="sidebar-brand-mark" aria-hidden="true"><Navigation24Regular /></div>
        <div className="sidebar-brand-copy">
          <strong>Server Network Assist</strong>
          <span>本机网络运维控制台</span>
        </div>
      </div>
      <nav className="sidebar-nav" aria-label="功能导航">
        {navigationItems.map(item => <button
          key={item.id}
          type="button"
          className="nav-button"
          data-section={item.id}
          aria-current={currentSection === item.id ? 'page' : undefined}
          onClick={() => navigate(item.id)}
          title={item.description}
        >
          {item.icon}
          <span className="nav-button-label">{item.label}</span>
        </button>)}
      </nav>
      <div className="sidebar-footer">
        <div className="sidebar-status">
          <span id="sidebar-status-dot" className={`status-dot ${status?.background?.running ? 'ok' : 'warning'}`} aria-hidden="true" />
          <span id="sidebar-status">{status?.background?.running ? '后台服务运行中' : '后台状态待确认'}</span>
        </div>
        <span id="version">版本 {status?.version ? `v${status.version}` : '—'}</span>
        <span id="platform" className="muted">{status?.platform || '本机桌面环境'}</span>
      </div>
    </aside>
    <div className="main-column">
      <header className="page-header">
        <div className="page-header-leading">
          <Button
            id="menu"
            className="mobile-only"
            appearance="subtle"
            icon={<Navigation24Regular />}
            aria-label="打开导航"
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen(true)}
          />
          <h1 id="page-title">{pageTitles[currentSection]}</h1>
        </div>
        <div className="page-header-meta">
          <StatusPill level={level}>{networkLabel(status)}</StatusPill>
          <span id="header-hostname" className="mono">{status?.hostname || '主机名待读取'}</span>
        </div>
        <div className="header-actions">
          <Tooltip content="重新读取本机状态" relationship="label">
            <Button id="refresh" appearance="subtle" icon={<ArrowClockwise24Regular />} aria-label="刷新" disabled={busy} onClick={onRefresh} />
          </Tooltip>
          <Select id="theme" aria-label="切换外观" value={themeMode} onChange={event => onThemeModeChange(event.target.value as ThemeMode)}>
            <option value="system">跟随系统</option>
            <option value="light">浅色</option>
            <option value="dark">深色</option>
          </Select>
        </div>
      </header>
      <main id="main-content" className="page-content">{children}</main>
    </div>
  </div>;
}
