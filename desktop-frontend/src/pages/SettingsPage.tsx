import { useEffect, useRef, useState } from 'react';
import { Button } from '@fluentui/react-components';
import { ArrowClockwise24Regular, Open24Regular } from '@fluentui/react-icons';
import type { StatusResponse, UpdateResponse } from '../api/types';
import { getUpdates } from '../api/updates';
import type { RunTask } from '../app/types';
import { PageIntro, StatusPill, Surface } from '../app/ui';

interface SettingsPageProps {
  status: StatusResponse | null;
  updates: UpdateResponse | null;
  notify: (message: string) => void;
  runTask: RunTask;
}

export function SettingsPage({status, updates: initialUpdates, notify, runTask}: SettingsPageProps) {
  const [updates, setUpdates] = useState<UpdateResponse | null>(initialUpdates);
  const manualCheckRef = useRef(false);
  const background = status?.background;

  useEffect(() => {
    if (!manualCheckRef.current) setUpdates(initialUpdates);
  }, [initialUpdates]);

  const checkUpdates = async () => {
    manualCheckRef.current = true;
    if (!await runTask(async () => setUpdates(await getUpdates()))) return;
    notify('更新检查已完成。不会自动安装新版本。');
  };

  const updateLinks = [{name: '查看 GitHub 发布说明', url: updates?.release_url}, ...(updates?.assets || [])].filter(link => {
    if (!link.url) return false;
    try {
      const url = new URL(link.url);
      return url.protocol === 'https:' && url.hostname === 'github.com';
    } catch {
      return false;
    }
  });

  return <>
    <PageIntro title="后台与更新" description="查看本机后台服务、监听范围和版本状态。更新检查只读取官方发布信息，不会自动安装未经确认的新版本。" action={<Button id="check-updates" appearance="primary" icon={<ArrowClockwise24Regular />} onClick={() => void checkUpdates()}>检查更新</Button>} />
    <div className="page-grid">
      <Surface title="后台服务状态" description="本程序的桌面面板仅绑定本机回环地址。">
        <div className="status-hero" style={{padding: 18}}>
          <div className="status-hero-main">
            <StatusPill level={background?.running ? 'ok' : 'warning'}>{background?.running ? '后台服务运行中' : '后台状态待确认'}</StatusPill>
            <p id="background-state">{background?.running ? '后台服务正在运行。' : '后台能力状态未提供。'}</p>
          </div>
          <div className="status-hero-aside"><span>监听范围</span><strong className="mono">127.0.0.1</strong><span>仅监听本机回环地址</span></div>
        </div>
        <dl className="definition-grid" style={{marginTop: 18}}>
          <div><dt>运行模式</dt><dd id="runtime-badge">桌面本机控制台</dd></div>
          <div><dt>系统托盘</dt><dd id="tray-state">{background?.tray_running ? `系统托盘已运行；状态通知${background.notifications_enabled ? '已开启' : '未开启'}。` : background?.tray_available ? '系统支持托盘；当前后台启动方式未运行托盘。' : '当前会话无法使用系统托盘。'}</dd></div>
          <div><dt>数据目录</dt><dd id="data-directory">后台未提供路径；本页不猜测本机数据位置。</dd></div>
          <div><dt>当前版本</dt><dd id="settings-version" className="mono">{status?.version ? `v${status.version}` : '—'}</dd></div>
        </dl>
      </Surface>
      <Surface title="更新状态" description="发布说明和安装包链接仅接受 HTTPS GitHub 地址。">
        <dl className="definition-grid">
          <div><dt>当前版本</dt><dd id="update-current" className="mono">{updates?.current || status?.version || '—'}</dd></div>
          <div><dt>最新版本</dt><dd id="update-latest" className="mono">{updates?.latest || '尚未检查'}</dd></div>
          <div className="span-2"><dt>状态</dt><dd id="update-state">{updates?.error || (updates ? `当前 ${updates.current || '—'} · 最新 ${updates.latest || '尚无发布版本'}${updates.available ? ' · 有更新' : ' · 无可用更新'}` : '尚未检查更新')}</dd></div>
        </dl>
        <div id="update-links" className="form-actions" style={{justifyContent: 'flex-start'}}>{updateLinks.map((link, index) => <Button key={`${link.url}-${index}`} as="a" href={link.url} target="_blank" rel="noopener noreferrer" appearance="subtle" icon={<Open24Regular />}>{link.name || '查看发布资源'}</Button>)}</div>
      </Surface>
    </div>
    <p className="muted">后台状态以实际服务返回为准；未知状态不会被显示成正常。</p>
  </>;
}
