import { useState } from 'react';
import { Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Field, Input, Select } from '@fluentui/react-components';
import { ArrowClockwise24Regular, ArrowRight24Regular, Dismiss24Regular, LockClosed24Regular } from '@fluentui/react-icons';
import type { CampusStatus, RecoveryRecord, StatusResponse, TunnelInfo } from '../api/types';
import { getCampus, getCampusStatus, loginCampus } from '../api/campus';
import { runTunnelAction } from '../api/status';
import { ButtonRow, EmptyState, formatAge, formatBytes, formatTime, PageIntro, StatusPill, Surface, unknown } from '../app/ui';

interface TunnelsPageProps {
  status: StatusResponse | null;
  runTask: (operation: () => Promise<void>, confirmation?: {title: string; body: string}) => Promise<boolean>;
  refreshStatus: () => Promise<StatusResponse | null>;
  notify: (message: string) => void;
}

function tunnelLevel(tunnel: TunnelInfo, timestamp?: number): 'ok' | 'warning' {
  if (!tunnel.active) return 'warning';
  if (!tunnel.handshake || !timestamp || timestamp - tunnel.handshake > 180) return 'warning';
  return 'ok';
}

function TunnelItem({tunnel, timestamp, elevated, recovery, runTask, refreshStatus, notify}: {
  tunnel: TunnelInfo;
  timestamp?: number;
  elevated: boolean;
  recovery?: RecoveryRecord;
  runTask: TunnelsPageProps['runTask'];
  refreshStatus: TunnelsPageProps['refreshStatus'];
  notify: TunnelsPageProps['notify'];
}) {
  const level = tunnelLevel(tunnel, timestamp);
  const disabled = ['Disabled', 'masked'].includes(tunnel.start_mode || '');
  const perform = async (action: 'connect' | 'disconnect' | 'pause-sharing' | 'restore-startup', confirmation: {title: string; body: string}) => {
    if (!await runTask(async () => {
      await runTunnelAction(action, tunnel.name);
      await refreshStatus();
    }, confirmation)) return;
    notify(action === 'pause-sharing' ? '已停止本机借网并禁用自动启动。请核对物理网关、代理和旧脚本额外路由，再登录校园网。' : '隧道操作已完成，请核对最新连通性。');
  };
  const age = formatAge(tunnel.handshake, timestamp);

  return <article className="tunnel-item">
    <div className="tunnel-item-header">
      <div>
        <h4>{tunnel.name}</h4>
        <p>服务状态：{unknown(tunnel.service_state)} · 启动方式：{unknown(tunnel.start_mode)}</p>
      </div>
      <StatusPill level={level}>{tunnel.active ? (level === 'ok' ? '已连接' : '握手待复核') : '已断开'}</StatusPill>
    </div>
    <dl className="tunnel-facts">
      <div><dt>最近握手</dt><dd>{age}</dd></div>
      <div><dt>RX</dt><dd className="mono">{tunnel.telemetry === false ? '—' : formatBytes(tunnel.received)}</dd></div>
      <div><dt>TX</dt><dd className="mono">{tunnel.telemetry === false ? '—' : formatBytes(tunnel.sent)}</dd></div>
      <div><dt>本地地址</dt><dd className="mono">{(tunnel.addresses || []).join(' / ') || '—'}</dd></div>
    </dl>
    <details>
      <summary>查看 Endpoint 与服务详情</summary>
      <dl className="definition-grid" style={{marginTop: 14}}>
        <div><dt>Endpoint</dt><dd className="mono">{tunnel.endpoint || '未提供'}</dd></div>
        <div><dt>服务状态</dt><dd>{unknown(tunnel.service_state)}</dd></div>
      </dl>
    </details>
    <div className="tunnel-item-footer">
      <span className="muted">计数器：{tunnel.telemetry === false ? '不可用' : 'WireGuard'}</span>
      <ButtonRow>
        <Button
          appearance={tunnel.active ? 'outline' : 'primary'}
          disabled={!elevated || (!tunnel.active && disabled)}
          onClick={() => perform(tunnel.active ? 'disconnect' : 'connect', tunnel.active
            ? {title: '临时断开借网隧道？', body: '将停止本机隧道；依赖隧道的连接可能中断。不会禁用下次自动启动。'}
            : {title: '连接本机隧道？', body: '将启动此 WireGuard 服务并改变本机隧道状态，请确认当前方案和权限。'})}
        >{tunnel.active ? '临时断开' : '连接'}</Button>
        <Button
          appearance="outline"
          disabled={!elevated}
          icon={<LockClosed24Regular />}
          onClick={() => perform('pause-sharing', {title: '停止借网并禁用自动启动？', body: '先备份启动配置，再停止并禁用此本机隧道服务。依赖隧道的连接可能中断；不会猜测删除旧脚本额外路由或恢复代理。'})}
        >停止借网</Button>
        {recovery && <Button appearance="outline" disabled={!elevated} onClick={() => perform('restore-startup', {title: '恢复原借网服务配置？', body: `将恢复 ${tunnel.name} 的启动方式与原运行状态。恢复完成后请重新检查连接状态。`})}>恢复配置</Button>}
      </ButtonRow>
    </div>
  </article>;
}

function CampusPanel({runTask, notify}: Pick<TunnelsPageProps, 'runTask' | 'notify'>) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [current, setCurrent] = useState<CampusStatus | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [service, setService] = useState('0');
  const [confirmed, setConfirmed] = useState(false);

  const checkScript = async () => {
    if (!await runTask(async () => setConfigured((await getCampus()).configured === true))) return;
    notify('校园网脚本状态已读取。');
  };
  const checkCurrent = async () => {
    if (!await runTask(async () => setCurrent(await getCampusStatus()))) return;
    notify('只读查询已完成，没有执行登录或下线。');
  };
  const submit = async () => {
    setPassword('');
    if (!await runTask(async () => {
      const result = await loginCampus({username, password, service, physical_network_confirmed: confirmed});
      setCurrent(result.current || null);
      setDialogOpen(false);
      setConfirmed(false);
    })) return;
    notify('校园网认证请求已完成，请查看当前状态复核账号与运营商。');
  };

  return <Surface title="恢复物理网络后登录校园网" description="校园网登录与 WireGuard 隧道分开管理。登录前必须确认本机隧道已完全停止，当前用户代理与 PAC 已关闭。">
    <div className="definition-grid">
      <div><dt>脚本状态</dt><dd>{configured === null ? '尚未检查' : configured ? '已安装，可在物理网络恢复后登录' : '未安装或未配置'}</dd></div>
      <div><dt>当前出口认证</dt><dd id="campus-state">{current ? `${current.state || 'unknown'} · ${current.account || '账号未确认'} · ${current.ip || 'IP 未确认'}` : '尚未查询'}</dd></div>
    </div>
    <p className="muted" style={{marginBottom: 0}}>账号和密码仅用于本次请求；密码提交后立即清空，不会显示在日志或状态摘要中。</p>
    <div className="form-actions">
      <Button id="campus-check" appearance="subtle" icon={<ArrowClockwise24Regular />} onClick={checkScript}>检查登录脚本</Button>
      <Button id="campus-status" appearance="subtle" onClick={checkCurrent}>查询当前状态</Button>
      <Button id="campus-open" appearance="primary" icon={<ArrowRight24Regular />} onClick={() => setDialogOpen(true)}>打开校园登录</Button>
    </div>
    <Dialog open={dialogOpen} onOpenChange={(_, data) => setDialogOpen(data.open)}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle action={<Button appearance="subtle" aria-label="关闭" icon={<Dismiss24Regular />} onClick={() => setDialogOpen(false)} />}>恢复物理网络后登录校园网</DialogTitle>
          <DialogContent>
            <form id="campus-form" onSubmit={event => {event.preventDefault(); void submit();}}>
              <div className="form-grid">
                <Field label="校园网账号" required><Input id="campus-username" value={username} onChange={(_, data) => setUsername(data.value)} /></Field>
                <Field label="密码（仅本次使用）" required><Input id="campus-password" type="password" value={password} onChange={(_, data) => setPassword(data.value)} /></Field>
                <Field label="运营商"><Select id="campus-service" value={service} onChange={event => setService(event.target.value)}>
                  <option value="0">校园网</option><option value="1">中国移动</option><option value="2">中国联通</option><option value="3">中国电信</option>
                </Select></Field>
                <div className="span-2"><Checkbox id="campus-physical" checked={confirmed} onChange={(_, data) => setConfirmed(Boolean(data.checked))} label="我已恢复本机物理网络，并确认 WireGuard、手动代理和 PAC 已停止或关闭。" /></div>
              </div>
              <DialogActions>
                <Button type="button" appearance="secondary" onClick={() => setDialogOpen(false)}>取消</Button>
                <Button type="submit" appearance="primary" disabled={!username.trim() || !password || !confirmed}>登录并复核</Button>
              </DialogActions>
            </form>
          </DialogContent>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  </Surface>;
}

export function TunnelsPage({status, runTask, refreshStatus, notify}: TunnelsPageProps) {
  const tunnels = status?.tunnels || [];
  return <>
    <PageIntro title="本机隧道" description="每个 WireGuard 隧道的服务状态、最近握手和计数器集中在这里。停止借网属于危险操作，会与普通临时断开区分。" />
    <Surface title="WireGuard 隧道" description="租约或服务状态为活动不等于公网可用；请结合最近握手、实际探测和连接概览判断。">
      <div id="tunnels" className="tunnel-list">
        {!tunnels.length && <EmptyState>尚未发现已安装的隧道。可以在“共享网络”创建方案。</EmptyState>}
        {tunnels.map(tunnel => <TunnelItem
          key={tunnel.name}
          tunnel={tunnel}
          timestamp={status?.timestamp}
          elevated={status?.elevated === true}
          recovery={(status?.recovery || []).find(record => record.name === tunnel.name && record.state !== 'restored')}
          runTask={runTask}
          refreshStatus={refreshStatus}
          notify={notify}
        />)}
      </div>
    </Surface>
    <CampusPanel runTask={runTask} notify={notify} />
    {status && <p className="muted" style={{marginTop: 14}}>本页状态读取于 {formatTime(status.timestamp)}。本机权限：{status.elevated ? '已就绪' : '需要管理员权限'}。</p>}
  </>;
}
