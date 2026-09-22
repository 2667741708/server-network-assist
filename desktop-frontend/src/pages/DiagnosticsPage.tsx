import { useMemo, useState } from 'react';
import { Button, Field, Select } from '@fluentui/react-components';
import { ArrowClockwise24Regular, ArrowDownload24Regular, Warning24Regular } from '@fluentui/react-icons';
import type { DiagnosticResponse, FleetAuditEvent, FleetState, StatusResponse } from '../api/types';
import { disableProfile, getAudit } from '../api/fleet';
import { runTunnelAction } from '../api/status';
import { sharingActions } from '../app/sharingState';
import type { DataFreshness, RunTask } from '../app/types';
import { ButtonRow, EmptyState, formatTime, PageIntro, StatusPill, Surface } from '../app/ui';

interface DiagnosticsPageProps {
  status: StatusResponse | null;
  statusFreshness: DataFreshness;
  fleet: FleetState;
  fleetFreshness: DataFreshness;
  diagnostics: DiagnosticResponse | null;
  loadDiagnostics: () => Promise<DiagnosticResponse>;
  refreshStatus: (force?: boolean) => Promise<StatusResponse | null>;
  refreshFleet: () => Promise<FleetState>;
  runTask: RunTask;
  notify: (message: string) => void;
}

type CheckLevel = 'ok' | 'warning' | 'danger' | 'info';

function checkRow(label: string, detail: string, level: CheckLevel, status: string) {
  return <div className="diagnostic-item" key={label}>
    <div className="diagnostic-main"><strong>{label}</strong><p>{detail}</p></div>
    <StatusPill level={level}>{status}</StatusPill>
  </div>;
}

function GuidanceList({items}: {items: DiagnosticResponse['guidance']}) {
  if (!items?.length) return <EmptyState>没有可显示的建议。</EmptyState>;
  return <div id="guidance" className="guidance-list">{items.map((item, index) => {
    const level = item.severity === 'error' ? 'danger' : item.severity === 'warning' ? 'warning' : 'info';
    return <div className="guidance-item" key={`${item.code || item.title || 'guidance'}-${index}`}>
      <div className="guidance-main"><h4>{item.title || '建议'}</h4><p>{item.detail || ''}</p></div>
      <StatusPill level={level}>{item.severity === 'error' ? 'Failed' : item.severity === 'warning' ? 'Warning' : '建议'}</StatusPill>
    </div>;
  })}</div>;
}

function localEventText(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (detail === undefined || detail === null) return '';
  try { return JSON.stringify(detail); } catch { return '事件详情不可序列化'; }
}

function EventList({events, fleetEvents, kind}: {events: DiagnosticResponse['events']; fleetEvents: FleetAuditEvent[]; kind: 'local' | 'fleet'}) {
  if (kind === 'local') {
    if (!events?.length) return <EmptyState>尚无本机操作记录。</EmptyState>;
    return <div id="diagnostic-events" className="event-list">{events.map((event, index) => <div className="event-item" key={`${event.timestamp || 0}-${index}`}><strong>{formatTime(event.timestamp)} · {event.action || '事件'}</strong><p>{event.outcome || '未知'} {localEventText(event.detail)}</p></div>)}</div>;
  }
  if (!fleetEvents.length) return <EmptyState>尚无多机操作记录。</EmptyState>;
  return <div id="fleet-events" className="event-list">{fleetEvents.map((event, index) => <div className="event-item" key={`${event.created_at || 0}-${index}`}><strong>{formatTime(event.created_at)} · {event.action || '事件'}</strong><p className="mono">{event.target || '未指定目标'} · {localEventText(event.details)}</p></div>)}</div>;
}

export function DiagnosticsPage({status, statusFreshness, fleet, fleetFreshness, diagnostics, loadDiagnostics, refreshStatus, refreshFleet, runTask, notify}: DiagnosticsPageProps) {
  const [fleetEvents, setFleetEvents] = useState<FleetAuditEvent[]>([]);
  const [selectedProfile, setSelectedProfile] = useState('');
  const effectiveStatus = diagnostics?.status || status;
  const hosts = fleet.hosts;
  const profiles = fleet.profiles;

  const checks = useMemo(() => {
    const direct = effectiveStatus?.direct?.ok;
    const system = effectiveStatus?.system?.ok;
    const tunnels = effectiveStatus?.tunnels || [];
    const active = tunnels.filter(tunnel => tunnel.active);
    const stale = active.some(tunnel => !tunnel.handshake || !effectiveStatus?.timestamp || effectiveStatus.timestamp - tunnel.handshake > 180);
    const routes = effectiveStatus?.routes || [];
    const untrusted = hosts.filter(host => !host.host_key).length;
    const recovery = profiles.filter(profile => profile.cleanup_pending || profile.state === 'error').length;
    const enabled = profiles.filter(profile => profile.state === 'enabled').length;
    return [
      {label: '公网', detail: direct === true ? '直连 HTTPS 可用' : direct === false ? '直连 HTTPS 未通过' : '未获得直连结果', level: direct === true ? 'ok' : direct === false ? 'danger' : 'info', status: direct === true ? '正常' : direct === false ? 'Failed' : 'Unknown'},
      {label: '代理 / 应用路径', detail: system === true ? '系统应用联网探测可用' : system === false ? '系统应用联网探测未通过' : '未获得系统应用结果', level: system === true ? 'ok' : system === false ? 'warning' : 'info', status: system === true ? '正常' : system === false ? 'Warning' : 'Unknown'},
      {label: '隧道', detail: tunnels.length ? `${active.length} / ${tunnels.length} 个隧道已连接${stale ? '，存在握手未更新' : ''}` : '未发现本机 WireGuard 隧道', level: stale ? 'warning' : active.length ? 'ok' : 'warning', status: stale ? 'Warning' : active.length ? '正常' : 'Warning'},
      {label: '路由', detail: routes.length ? `已读取 ${routes.length} 条默认路由` : '没有可用的默认路由信息', level: routes.length ? 'ok' : 'warning', status: routes.length ? '正常' : 'Warning'},
      {label: 'SSH', detail: hosts.length ? (untrusted ? `${untrusted} 台主机待确认指纹` : `${hosts.length} 台主机已固定公钥`) : '尚未配置远端主机', level: hosts.length && !untrusted ? 'ok' : hosts.length ? 'warning' : 'info', status: hosts.length && !untrusted ? '正常' : hosts.length ? 'Warning' : '未配置'},
      {label: '共享方案', detail: profiles.length ? (recovery ? `${recovery} 个方案需要恢复处理` : enabled ? `${enabled} 个方案正在运行` : '方案已保存但未启用') : '尚未配置共享方案', level: recovery ? 'danger' : enabled ? 'ok' : 'info', status: recovery ? 'Failed' : enabled ? '正常' : '未启用'},
    ] as Array<{label: string; detail: string; level: CheckLevel; status: string}>;
  }, [effectiveStatus, hosts, profiles]);

  const refresh = async () => {
    if (!await runTask(async () => {
      await loadDiagnostics();
      try {
        const audit = await getAudit();
        setFleetEvents(audit.events || []);
      } catch {
        setFleetEvents([]);
      }
    })) return;
    notify('诊断完成；请按真实结果选择恢复操作。');
  };

  const exportReport = () => {
    if (!diagnostics) return;
    const blob = new Blob([JSON.stringify(diagnostics, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `network-diagnostics-${Date.now()}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const restoreSharing = async () => {
    const profile = profiles.find(item => item.id === selectedProfile);
    if (!profile) {
      notify('请选择要恢复的共享方案。');
      return;
    }
    if (fleetFreshness !== 'fresh' || !sharingActions(profile, fleetFreshness).disable) {
      notify('共享方案状态未知或不允许恢复，请先重新读取 Fleet 状态。');
      return;
    }
    if (!await runTask(async () => {
      await disableProfile(profile.id);
    }, {title: '恢复共享网络？', body: `将对方案“${profile.name}”执行现有恢复流程，清理它拥有的隧道与临时路由；客户端网络可能短暂中断。`}, async () => {
      await refreshStatus(true);
      await refreshFleet();
      await loadDiagnostics();
    })) return;
    notify('共享网络恢复请求已完成，请重新读取诊断状态。');
  };

  const restoreProxy = async () => {
    if (!await runTask(async () => {
      await runTunnelAction('disable-proxy');
    }, {title: '恢复代理？', body: '将通过现有代理备份逻辑关闭当前用户手动代理；PAC 不会因此被移除。恢复后请查看系统代理页面。'}, async () => {
      await refreshStatus(true);
      await loadDiagnostics();
    })) return;
    notify('代理恢复请求已完成，请重新读取代理状态。');
  };

  const checkTunnels = async () => {
    if (!await runTask(async () => {
      return;
    }, undefined, async () => {
      await refreshStatus(true);
      await loadDiagnostics();
    })) return;
    notify('隧道检查已完成。');
  };

  return <>
    <PageIntro title="诊断与恢复" description="这页把当前问题、后端建议、本机事件和 Fleet 审计分开显示。恢复按钮只调用已有安全 API，并且危险操作需要确认。" action={<ButtonRow><Button id="run-diagnostics" appearance="primary" icon={<ArrowClockwise24Regular />} onClick={() => void refresh()}>重新运行诊断</Button><Button id="export-diagnostics" appearance="subtle" icon={<ArrowDownload24Regular />} disabled={!diagnostics} onClick={exportReport}>导出报告</Button></ButtonRow>} />
    <Surface title="当前问题" description="正常、Warning、Failed 和 Unknown 分别对应真实检测结果，不根据文本猜测动作。">
      <div id="diagnostic-summary" className="diagnostic-list">{checks.map(check => checkRow(check.label, check.detail, check.level, check.status))}</div>
    </Surface>
    <div className="page-grid">
      <Surface title="建议操作" description="建议来源于后台诊断结果；执行前请确认影响范围。"><div id="guidance"><GuidanceList items={diagnostics?.guidance} /></div></Surface>
      <Surface title="恢复入口" description="选择明确对象后再执行，所有写操作都使用现有 typed backend API。">
        <Field label="共享方案"><Select value={selectedProfile} onChange={event => setSelectedProfile(event.target.value)}><option value="">选择需要恢复的方案</option>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name} · {profile.state}</option>)}</Select></Field>
        <div className="form-actions" style={{justifyContent: 'flex-start'}}>
          <Button data-go="restore-sharing" appearance="outline" icon={<Warning24Regular />} disabled={fleetFreshness !== 'fresh' || statusFreshness !== 'fresh'} onClick={() => void restoreSharing()}>恢复共享网络</Button>
          <Button data-go="restore-proxy" appearance="outline" disabled={statusFreshness !== 'fresh'} onClick={() => void restoreProxy()}>恢复代理</Button>
          <Button data-go="check-tunnel" appearance="subtle" onClick={() => void checkTunnels()}>检查隧道</Button>
        </div>
      </Surface>
    </div>
    <div className="page-grid">
      <Surface title="本机事件" description="本机操作记录，可能因日志能力不可用而为空。"><EventList events={diagnostics?.events} fleetEvents={[]} kind="local" /></Surface>
      <Surface title="Fleet 审计" description="远端主机和共享方案的审计记录，不与本机事件混合。"><EventList events={[]} fleetEvents={fleetEvents} kind="fleet" /></Surface>
    </div>
    {diagnostics?.error && <p className="muted">诊断读取错误：{diagnostics.error}</p>}
  </>;
}
