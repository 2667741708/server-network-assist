import { Button } from '@fluentui/react-components';
import { ArrowRight24Regular, ArrowSync24Regular } from '@fluentui/react-icons';
import type { StatusResponse, TrafficInfo, TunnelInfo } from '../api/types';
import type { SectionId } from '../app/types';
import { EmptyState, formatBytes, formatClock, formatRate, PageIntro, StatusPill, Surface, unknown } from '../app/ui';

interface OverviewPageProps {
  status: StatusResponse | null;
  onNavigate: (section: SectionId) => void;
}

interface NetworkSummary {
  level: 'ok' | 'warning' | 'danger' | 'info';
  label: string;
  title: string;
  description: string;
}

function getNetworkSummary(status: StatusResponse | null): NetworkSummary {
  if (!status) return {level: 'info', label: '状态待确认', title: '正在读取连接状态', description: '首次读取可能需要几秒；未知值不会被当作正常或零流量。'};
  const direct = status.direct?.ok;
  const system = status.system?.ok;
  const now = status.timestamp || 0;
  const tunnelIssue = (status.tunnels || []).some(tunnel => tunnel.active && (!tunnel.handshake || now - tunnel.handshake > 180));
  if (direct === false && system === false) return {level: 'danger', label: '无法访问公网', title: '无法访问公网', description: '直连和系统应用路径都未通过，请查看路由、隧道和远端探测。'};
  if (direct === true && system === false) return {level: 'warning', label: '系统代理异常', title: '直连正常，系统应用路径异常', description: '直连 HTTPS 成功，但系统应用路径失败，请检查当前用户代理和 PAC。'};
  if (direct === false && system === true) return {level: 'warning', label: '部分异常', title: '系统应用仍可用，直连检测失败', description: '当前应用路径仍可用，但本机直连公网检测未通过。'};
  if (tunnelIssue) return {level: 'warning', label: '隧道异常', title: '隧道握手需要复核', description: '存在活动隧道的握手未更新，请结合实际服务探测判断，不要只依据租约状态。'};
  if (direct == null || system == null) return {level: 'info', label: '状态待确认', title: '连接状态待确认', description: '部分检测结果不可用，未知值不会被当作正常或零流量。'};
  return {level: 'ok', label: '连接运行正常', title: '网络已就绪', description: '原生网络与系统应用均可访问公网。'};
}

function statusLevel(value: boolean | null | undefined, warning = false): 'ok' | 'warning' | 'danger' | 'info' {
  if (value === true) return 'ok';
  if (value === false) return warning ? 'warning' : 'danger';
  return 'info';
}

function statusText(value: boolean | null | undefined): string {
  return value === true ? '可用' : value === false ? '不可用' : '未知';
}

function totalTunnelTraffic(tunnels: TunnelInfo[] | undefined): {total: number | null; received: number | null; sent: number | null} {
  const measured = (tunnels || []).filter(tunnel => tunnel.telemetry !== false && Number.isFinite(tunnel.received) && Number.isFinite(tunnel.sent));
  if (!measured.length) return {total: null, received: null, sent: null};
  const received = measured.reduce((sum, tunnel) => sum + (tunnel.received || 0), 0);
  const sent = measured.reduce((sum, tunnel) => sum + (tunnel.sent || 0), 0);
  return {total: received + sent, received, sent};
}

function TrafficChart({traffic}: {traffic?: TrafficInfo}) {
  const points = (traffic?.history || []).filter(point => Number.isFinite(point.received_per_second) && Number.isFinite(point.sent_per_second));
  if (!traffic?.available || points.length < 2) {
    return <div id="traffic-chart" className="empty-state">暂无足够的 WireGuard 计数器样本；未知速率不会显示为零。</div>;
  }
  const width = 700;
  const height = 150;
  const padding = 20;
  const max = Math.max(...points.flatMap(point => [point.received_per_second || 0, point.sent_per_second || 0]), 1);
  const toPath = (key: 'received_per_second' | 'sent_per_second') => points.map((point, index) => {
    const x = padding + index * ((width - padding * 2) / (points.length - 1));
    const y = height - padding - ((point[key] || 0) / max) * (height - padding * 2);
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
  return <div id="traffic-chart" className="chart-box" role="img" aria-label="WireGuard 隧道收发速率趋势图">
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
      <title>WireGuard 隧道收发速率</title>
      <line x1={padding} x2={width - padding} y1={height - padding} y2={height - padding} stroke="currentColor" opacity="0.25" />
      <line x1={padding} x2={width - padding} y1={padding} y2={padding} stroke="currentColor" opacity="0.12" />
      <path d={toPath('received_per_second')} fill="none" stroke="#0f6cbd" strokeWidth="3" strokeLinecap="round" />
      <path d={toPath('sent_per_second')} fill="none" stroke="#107c41" strokeWidth="3" strokeLinecap="round" />
      <text className="chart-axis-label" x={padding} y={height - 3}>较早</text>
      <text className="chart-axis-label" x={width - padding} y={height - 3} textAnchor="end">最近</text>
    </svg>
    <div className="table-actions" aria-label="图表图例">
      <span className="muted"><span className="status-dot info" aria-hidden="true" /> 下载 B/s</span>
      <span className="muted"><span className="status-dot ok" aria-hidden="true" /> 上传 B/s</span>
    </div>
  </div>;
}

function RouteList({status}: {status: StatusResponse}) {
  const routes = status.routes || [];
  if (!routes.length) return <EmptyState>没有可用的默认路由信息。</EmptyState>;
  return <div className="event-list" id="routes">
    {routes.map((route, index) => <div className="event-item" key={`${route.adapter || 'route'}-${index}`}>
      <strong>{route.adapter || '未识别网卡'}</strong>
      <p><span className="mono">网关 {unknown(route.gateway)}</span> · metric {route.metric == null ? '—' : route.metric}</p>
    </div>)}
  </div>;
}

export function OverviewPage({status, onNavigate}: OverviewPageProps) {
  const summary = getNetworkSummary(status);
  const traffic = totalTunnelTraffic(status?.tunnels);
  const activeTunnels = (status?.tunnels || []).filter(tunnel => tunnel.active).length;
  const tunnelCount = status?.tunnels?.length || 0;
  const direct = status?.direct?.ok;
  const system = status?.system?.ok;
  const proxy = status?.proxy;

  return <>
    <PageIntro title="连接概览" description="先确认当前公网、系统应用和本机隧道状态，再进入需要操作的页面。所有状态均来自现有后台检测。" action={<Button appearance="subtle" icon={<ArrowSync24Regular />} onClick={() => onNavigate('diagnostics')}>查看诊断与恢复</Button>} />
    <section id="network-summary" className="surface status-hero" data-state={summary.level}>
      <div className="status-hero-main">
        <StatusPill level={summary.level}><span id="network-dot" className={`status-dot ${summary.level}`} aria-hidden="true" />{summary.label}</StatusPill>
        <h2 id="network-title">{summary.title}</h2>
        <p id="network-description">{summary.description}</p>
      </div>
      <div className="status-hero-aside">
        <span>当前主机</span>
        <strong id="hero-hostname" className="mono">{status?.hostname || '—'}</strong>
        <span id="hero-platform" className="muted">{status?.platform || '—'}</span>
        <span id="hero-permission" className="muted">{status?.elevated ? '网络控制权限已就绪' : '本机连接控制需要管理员权限'}</span>
        <span id="updated" className="muted">更新于 {formatClock(status?.timestamp)}</span>
      </div>
    </section>

    <div className="summary-grid" aria-label="核心状态">
      <div className="surface metric-card">
        <span className="metric-label">直连公网</span>
        <strong id="direct" className="metric-value"><StatusPill level={statusLevel(direct)}>{statusText(direct)}</StatusPill></strong>
        <span id="latency" className="metric-detail">{direct === true ? `HTTPS 响应 ${status?.direct?.milliseconds ?? '—'} ms` : 'HTTPS 连通性检测未通过'}</span>
      </div>
      <div className="surface metric-card">
        <span className="metric-label">系统应用联网</span>
        <strong id="system" className="metric-value"><StatusPill level={statusLevel(system, true)}>{statusText(system)}</StatusPill></strong>
        <span id="system-detail" className="metric-detail">{status?.system?.scope || '使用当前用户的代理设置'}</span>
      </div>
      <div className="surface metric-card">
        <span className="metric-label">隧道总流量</span>
        <strong id="traffic" className="metric-value">{formatBytes(traffic.total)}</strong>
        <span id="traffic-detail" className="metric-detail">↓ {formatBytes(traffic.received)}　↑ {formatBytes(traffic.sent)} · {activeTunnels} / {tunnelCount} 已连接</span>
      </div>
    </div>

    <div className="page-grid page-grid-wide">
      <Surface title="实时隧道速率" description="仅为 WireGuard 隧道流量，不是整机网卡流量。">
        <div id="traffic-rate" className="metric-value">↓ {formatBytes(status?.traffic?.received_per_second)} /s　↑ {formatBytes(status?.traffic?.sent_per_second)} /s</div>
        <p id="traffic-scope" className="muted">{status?.traffic?.scope || '仅为 WireGuard 隧道流量，不是整机网卡流量。'}</p>
        <TrafficChart traffic={status?.traffic} />
        <div id="traffic-history" className="table-wrap">
          {(status?.traffic?.history || []).length > 0 && <table className="data-table"><thead><tr><th>时间</th><th>下载</th><th>上传</th></tr></thead><tbody>
            {[...(status?.traffic?.history || [])].reverse().slice(0, 8).map(point => <tr key={point.timestamp}><td>{formatClock(point.timestamp)}</td><td className="mono">{formatRate(point.received_per_second)}</td><td className="mono">{formatRate(point.sent_per_second)}</td></tr>)}
          </tbody></table>}
        </div>
      </Surface>
      <Surface title="本机出口" description="默认路由和当前用户代理是不同层次的状态。">
        <dl className="definition-grid">
          <div><dt>默认路由</dt><dd>{status?.routes?.length ? '已读取' : '未读取'} · {status?.routes?.length || 0} 条</dd></div>
          <div><dt>物理网卡</dt><dd className="mono">{status?.routes?.[0]?.adapter || '未识别'}</dd></div>
          <div><dt>网关</dt><dd className="mono">{status?.routes?.[0]?.gateway || '未识别'}</dd></div>
          <div><dt>代理状态</dt><dd id="overview-proxy-state">{system === false ? '需检查系统代理 / PAC' : proxy?.enabled ? '当前用户手动代理已开启' : '系统应用路径可用'}</dd></div>
        </dl>
        <div className="form-actions">
          <Button appearance="subtle" icon={<ArrowRight24Regular />} onClick={() => onNavigate('proxy-page')}>查看系统代理</Button>
        </div>
      </Surface>
    </div>

    <Surface title="默认路由" description="这里只展示后台实际读取到的默认路由，不推断整机网卡指标。">
      {status ? <RouteList status={status} /> : <EmptyState>正在读取路由状态。</EmptyState>}
    </Surface>
  </>;
}
