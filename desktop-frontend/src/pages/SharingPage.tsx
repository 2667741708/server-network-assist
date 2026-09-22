import { useEffect, useMemo, useState, type ChangeEvent } from 'react';
import { Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Field, Input, Select, Textarea } from '@fluentui/react-components';
import { Add24Regular, Dismiss24Regular, PlugConnected24Regular, Search24Regular, Warning24Regular } from '@fluentui/react-icons';
import type { FleetState, ProfileDraft, ProbeResult, SharingProfile } from '../api/types';
import { deleteProfile, disableProfile, enableProfile, installHelper, probeHosts, saveProfile } from '../api/fleet';
import { probeCapabilities, type ProbeRole } from '../app/probes';
import { sharingActionAllowed, sharingActions } from '../app/sharingState';
import type { DataFreshness, RunTask } from '../app/types';
import { ButtonRow, EmptyState, formatTime, PageIntro, StatusPill, Surface } from '../app/ui';

interface SharingPageProps {
  fleet: FleetState;
  fleetFreshness: DataFreshness;
  refreshFleet: () => Promise<FleetState>;
  refreshStatus: (force?: boolean) => Promise<unknown>;
  runTask: RunTask;
  notify: (message: string) => void;
}

const blankProfile: ProfileDraft = {id: '', name: '', gateway_id: '', client_ids: [], port: 51919, endpoint: '', tunnel_cidr: '', preserve_routes: '', maintenance: true, proxy_mode: 'direct', proxy_host: '127.0.0.1', proxy_port: 7897};

function profileDraftFrom(profile?: SharingProfile): ProfileDraft {
  if (!profile) return blankProfile;
  return {
    id: profile.id, name: profile.name, gateway_id: profile.gateway_id, client_ids: profile.client_ids || [], port: profile.port,
    endpoint: profile.endpoint, tunnel_cidr: profile.tunnel_cidr, preserve_routes: (profile.preserve_routes || []).join('\n'),
    maintenance: profile.maintenance, proxy_mode: profile.proxy_mode === 'share' ? 'share' : 'direct', proxy_host: profile.proxy_host, proxy_port: profile.proxy_port,
  };
}

function ProfileEditor({open, profile, fleet, refreshFleet, onClose, onSaved, runTask, notify}: {
  open: boolean;
  profile?: SharingProfile;
  fleet: FleetState;
  refreshFleet: () => Promise<FleetState>;
  onClose: () => void;
  onSaved: (profile: SharingProfile) => void;
  runTask: RunTask;
  notify: SharingPageProps['notify'];
}) {
  const [draft, setDraft] = useState<ProfileDraft>(profileDraftFrom(profile));

  useEffect(() => setDraft(profileDraftFrom(profile)), [profile]);

  const update = <K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) => setDraft(current => ({...current, [key]: value}));
  const selectedClients = (event: ChangeEvent<HTMLSelectElement>) => update('client_ids', Array.from(event.target.selectedOptions).map(option => option.value));

  const submit = async () => {
    const payload: SharingProfile = {
      ...draft,
      client_ids: draft.client_ids,
      preserve_routes: draft.preserve_routes.split(/[\s,]+/).map(item => item.trim()).filter(Boolean),
      state: profile?.state || 'disabled',
    };
    if (!await runTask(async () => {
      const result = await saveProfile(payload);
      if (result.profile) onSaved(result.profile);
      onClose();
    }, undefined, refreshFleet)) return;
    notify('共享方案已保存；保存不会自动启用方案。');
  };

  const clients = fleet.hosts.filter(host => host.id !== draft.gateway_id);
  return <Dialog open={open} onOpenChange={(_, data) => {if (!data.open) onClose();}}>
    <DialogSurface>
      <DialogBody>
        <DialogTitle action={<Button appearance="subtle" aria-label="关闭" icon={<Dismiss24Regular />} onClick={onClose} />}>{profile ? '编辑共享方案' : '新建共享方案'}</DialogTitle>
        <DialogContent>
          <form id="profile-form" onSubmit={event => {event.preventDefault(); void submit();}}>
            <div id="profile-fields" className="form-grid">
              <Field label="方案名称" required><Input id="profile-name" value={draft.name} onChange={(_, data) => update('name', data.value)} /></Field>
              <Field label="UDP 端口" required><Input id="profile-port" type="number" value={String(draft.port)} onChange={(_, data) => update('port', Number(data.value) || 0)} /></Field>
              <Field label="出口机" required><Select id="profile-gateway_id" value={draft.gateway_id} onChange={event => update('gateway_id', event.target.value)}><option value="">请选择出口机</option>{fleet.hosts.map(host => <option key={host.id} value={host.id}>{host.name} · {host.address}</option>)}</Select></Field>
              <Field label="Endpoint"><Input id="profile-endpoint" className="mono" value={draft.endpoint} onChange={(_, data) => update('endpoint', data.value)} placeholder="留空使用出口机地址" /></Field>
              <Field className="span-2" label="客户端（Ctrl / Command 可多选）" required><select id="profile-client_ids" className="fui-Select" multiple value={draft.client_ids} onChange={selectedClients}>{clients.map(host => <option key={host.id} value={host.id}>{host.name} · {host.address}</option>)}</select></Field>
              <Field label="隧道网段"><Input id="profile-tunnel_cidr" className="mono" value={draft.tunnel_cidr} onChange={(_, data) => update('tunnel_cidr', data.value)} placeholder="留空自动分配" /></Field>
              <Field label="源代理模式"><Select id="profile-proxy_mode" value={draft.proxy_mode} onChange={event => update('proxy_mode', event.target.value as ProfileDraft['proxy_mode'])}><option value="direct">仅共享网络，不共享源代理</option><option value="share">共享网络和源 HTTP/HTTPS 代理</option></Select></Field>
              <Field className="span-2" label="仍走原网络的 CIDR"><Textarea id="profile-preserve_routes" className="mono" value={draft.preserve_routes} onChange={(_, data) => update('preserve_routes', data.value)} placeholder="每行一个，也支持空格或逗号分隔" resize="vertical" /></Field>
              {draft.proxy_mode === 'share' && <><Field label="源机器代理 IPv4"><Input id="profile-proxy_host" className="mono" value={draft.proxy_host} onChange={(_, data) => update('proxy_host', data.value)} /></Field><Field label="源代理端口"><Input id="profile-proxy_port" type="number" value={String(draft.proxy_port)} onChange={(_, data) => update('proxy_port', Number(data.value) || 0)} /></Field></>}
              <div className="span-2"><Checkbox id="profile-maintenance" checked={draft.maintenance} onChange={(_, data) => update('maintenance', Boolean(data.checked))} label="持续维护隧道；连续失败后恢复原网络" /></div>
            </div>
            <div className="path-flow" aria-label="共享路径预览">
              <div className="path-node"><strong>Client</strong><span>客户端</span></div><div className="path-arrow" aria-hidden="true">→</div><div className="path-node"><strong>WireGuard</strong><span>加密隧道</span></div><div className="path-arrow" aria-hidden="true">→</div><div className="path-node"><strong>Source Host</strong><span>出口机</span></div><div className="path-arrow" aria-hidden="true">→</div><div className="path-node"><strong>{draft.proxy_mode === 'share' ? 'Source Proxy' : 'Internet'}</strong><span>{draft.proxy_mode === 'share' ? '源代理' : '公网'}</span></div>
            </div>
            <p className="muted">启用 WireGuard 不等于一定公网可用。启用源代理时，实际路径会变为 Client → WG → Source Proxy。</p>
            <DialogActions><Button type="button" appearance="secondary" onClick={onClose}>取消</Button><Button type="submit" appearance="primary" disabled={!draft.name || !draft.gateway_id || !draft.client_ids.length}>保存方案</Button></DialogActions>
          </form>
        </DialogContent>
      </DialogBody>
    </DialogSurface>
  </Dialog>;
}

function ProfileDetail({profile, fleet, fleetFreshness, refreshFleet, refreshStatus, runTask, notify, onEdit}: {
  profile: SharingProfile;
  fleet: FleetState;
  fleetFreshness: DataFreshness;
  refreshFleet: SharingPageProps['refreshFleet'];
  refreshStatus: SharingPageProps['refreshStatus'];
  runTask: SharingPageProps['runTask'];
  notify: SharingPageProps['notify'];
  onEdit: () => void;
}) {
  const [probes, setProbes] = useState<ProbeResult[]>([]);
  const hostName = (id: string) => fleet.hosts.find(host => host.id === id)?.name || id;
  const gateway = hostName(profile.gateway_id);
  const clientNames = profile.client_ids.map(hostName);
  const pathLast = profile.proxy_mode === 'share' ? 'Source Proxy' : 'Internet';
  const active = profile.state === 'enabled';
  const actions = sharingActions(profile, fleetFreshness);

  const action = async (kind: 'enable' | 'disable' | 'delete') => {
    if (!sharingActionAllowed(actions, kind)) {
      notify(actions.message || '当前服务端状态不允许执行该操作，请先刷新。');
      return;
    }
    const operation = kind === 'enable' ? enableProfile : kind === 'disable' ? disableProfile : deleteProfile;
    const confirmation = kind === 'enable'
      ? {title: '启用多机网络共享？', body: `出口：${gateway}；客户端：${clientNames.join('、')}。将改变客户端公网路由${profile.proxy_mode === 'share' ? '及用户代理' : ''}，复检失败会尝试回退。`}
      : kind === 'disable'
        ? {title: '断开并恢复原网络？', body: '将清理所选方案的隧道和临时路由，并恢复它更改的代理；相关下载和远程连接可能中断。'}
        : {title: '删除共享方案？', body: '将删除保存的方案；仍在运行或未清理完成的方案会被后台拒绝。'};
    if (!await runTask(async () => {await operation(profile.id);}, confirmation, async () => {
      await Promise.all([refreshFleet(), refreshStatus(true)]);
    })) return;
    notify(kind === 'enable' ? '共享方案启用请求已完成，请检查诊断结果。' : kind === 'disable' ? '共享方案恢复请求已完成，请检查原网络。' : '共享方案已删除。');
  };

  const probe = async () => {
    if (!await runTask(async () => setProbes((await probeHosts([profile.gateway_id, ...profile.client_ids])).results || []), undefined, refreshFleet)) return;
    notify('已完成所选主机的实际探测。');
  };

  const helper = async () => {
    if (!await runTask(async () => {await installHelper([profile.gateway_id, ...profile.client_ids]);}, {title: '安装远端辅助程序？', body: '将在所选出口机与客户端安装网络辅助程序，需要远端管理员权限。请确认主机选择和 SSH 指纹无误。'}, refreshFleet)) return;
    notify('辅助程序安装请求已完成，请再次探测确认各主机能力。');
  };

  return <>
    <Surface title="方案详情" description="先查看路径和影响范围，再选择启用、停止或恢复。" action={<Button appearance="subtle" onClick={onEdit} disabled={fleetFreshness !== 'fresh'}>编辑方案</Button>}>
      <div id="profile-path" className="path-flow" aria-label="共享路径">
        <div className="path-node"><strong>Client</strong><span>{clientNames.join('、') || '未选择客户端'}</span></div><div className="path-arrow" aria-hidden="true">→</div><div className="path-node"><strong>WireGuard</strong><span className="mono">{profile.tunnel_cidr}</span></div><div className="path-arrow" aria-hidden="true">→</div><div className="path-node"><strong>Source Host</strong><span>{gateway}</span></div><div className="path-arrow" aria-hidden="true">→</div><div className="path-node"><strong>{pathLast}</strong><span>{profile.proxy_mode === 'share' ? `${profile.proxy_host}:${profile.proxy_port}` : '公网路径'}</span></div>
      </div>
      <div className="page-grid">
        <div><p className="section-kicker">基本信息</p><dl className="definition-grid"><div><dt>状态</dt><dd><StatusPill level={active ? 'ok' : profile.cleanup_pending || profile.state === 'error' ? 'danger' : profile.state === 'enabling' || profile.state === 'disabling' ? 'warning' : 'info'}>{profile.cleanup_pending ? '恢复未完成' : profile.state === 'enabling' ? '正在启用' : profile.state === 'disabling' ? '正在停止' : profile.state === 'error' ? '错误' : profile.state === 'enabled' ? '已启用' : '已停用'}</StatusPill></dd></div><div><dt>方案 ID</dt><dd className="mono">{profile.id}</dd></div><div><dt>出口机</dt><dd>{gateway}</dd></div><div><dt>客户端</dt><dd>{clientNames.join('、') || '—'}</dd></div></dl></div>
        <div><p className="section-kicker">网络范围</p><dl className="definition-grid"><div><dt>隧道网段</dt><dd className="mono">{profile.tunnel_cidr}</dd></div><div><dt>Endpoint</dt><dd className="mono">{profile.endpoint}</dd></div><div><dt>保留原路由</dt><dd className="mono">{profile.preserve_routes.join(', ') || '无额外保留路由'}</dd></div><div><dt>持续维护</dt><dd>{profile.maintenance ? '开启' : '关闭'}</dd></div></dl></div>
      </div>
      <div className="surface" style={{marginTop: 18, boxShadow: 'none'}}><div className="surface-header"><div><h4>源代理</h4><p>源代理仅在方案明确开启时参与路径。</p></div></div><div className="surface-body"><p>{profile.proxy_mode === 'share' ? <>Client → WG → Source Proxy <span className="mono">{profile.proxy_host}:{profile.proxy_port}</span></> : '未共享源代理：Client → WG → Internet。'}</p></div></div>
      <div className="form-actions" style={{justifyContent: 'flex-start'}}><ButtonRow><Button id="enable-profile" appearance="primary" disabled={!actions.enable} icon={<PlugConnected24Regular />} onClick={() => void action('enable')}>启用共享</Button><Button id="disable-profile" appearance="outline" disabled={!actions.disable} icon={<Warning24Regular />} onClick={() => void action('disable')}>{actions.recovery ? '继续恢复' : '停止并恢复'}</Button><Button id="delete-profile" appearance="subtle" disabled={!actions.delete} onClick={() => void action('delete')}>删除方案</Button></ButtonRow></div>
      {actions.message && <p className="muted" role="status">{actions.message}</p>}
    </Surface>
    <Surface title="健康与恢复" description="实际探测用于确认主机能力；探测结果不自动触发危险操作。">
      <ButtonRow><Button id="probe-all" appearance="subtle" icon={<Search24Regular />} onClick={() => void probe()}>探测出口与客户端</Button><Button id="install-helper" appearance="subtle" disabled={fleetFreshness !== 'fresh'} onClick={() => void helper()}>安装辅助程序</Button></ButtonRow>
      <div id="probes" className="event-list" style={{marginTop: 14}}>{!probes.length ? <EmptyState>尚未执行本次方案探测。</EmptyState> : probes.map((probe, index) => {const role: ProbeRole = probe.id === profile.gateway_id ? 'gateway' : profile.client_ids.includes(String(probe.id)) ? 'client' : 'unknown'; return <div className="event-item" key={`${probe.id || 'probe'}-${index}`}><strong>{hostName(String(probe.id || '未知主机'))}</strong>{probeCapabilities(probe, role, profile.state).map(capability => <p key={capability.key}><StatusPill level={capability.level}>{capability.label} · {capability.detail}</StatusPill></p>)}{probe.error && <p className="muted">错误：{probe.error}</p>}</div>;})}</div>
      {profile.last_error && <p className="muted">上次错误：{profile.last_error}</p>}
      <p className="muted">最近保存：{formatTime(profile.updated_at)}</p>
    </Surface>
  </>;
}

export function SharingPage({fleet, fleetFreshness, refreshFleet, refreshStatus, runTask, notify}: SharingPageProps) {
  const [selectedId, setSelectedId] = useState('');
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorProfile, setEditorProfile] = useState<SharingProfile | undefined>();

  useEffect(() => {
    if (selectedId && fleet.profiles.some(profile => profile.id === selectedId)) return;
    setSelectedId(fleet.profiles[0]?.id || '');
  }, [fleet.profiles, selectedId]);

  const selected = useMemo(() => fleet.profiles.find(profile => profile.id === selectedId), [fleet.profiles, selectedId]);
  const openEditor = (profile?: SharingProfile) => {setEditorProfile(profile); setEditorOpen(true);};
  const onSaved = (profile: SharingProfile) => setSelectedId(profile.id);

  return <>
    <PageIntro title="共享网络" description="把方案列表和方案详情分开，先看 Client → WireGuard → Source Host → Internet 的实际路径，再执行影响客户端路由的操作。" action={<Button id="new-profile" appearance="primary" icon={<Add24Regular />} onClick={() => openEditor()}>新建共享方案</Button>} />
    <div className="page-grid page-grid-wide sharing-layout">
      <Surface title="方案列表" description={`${fleet.profiles.length} 个已保存方案`}>
        <div id="profiles" className="event-list">
          {!fleet.profiles.length ? <EmptyState>尚未创建共享方案。先在“主机与凭据”准备可信主机。</EmptyState> : fleet.profiles.map(profile => <button key={profile.id} type="button" className={`event-item ${profile.id === selectedId ? 'selected-list-item' : ''}`} onClick={() => setSelectedId(profile.id)} style={{textAlign: 'left', cursor: 'pointer'}}><span className="tunnel-item-header"><strong>{profile.name}</strong><StatusPill level={profile.state === 'enabled' ? 'ok' : profile.state === 'error' || profile.cleanup_pending ? 'danger' : 'info'}>{profile.cleanup_pending ? '恢复未完成' : profile.state}</StatusPill></span><p>{profile.client_ids.length} 个客户端 · 出口 {fleet.hosts.find(host => host.id === profile.gateway_id)?.name || profile.gateway_id}</p></button>)}
        </div>
      </Surface>
      <div id="profile-detail">{selected ? <ProfileDetail profile={selected} fleet={fleet} fleetFreshness={fleetFreshness} refreshFleet={refreshFleet} refreshStatus={refreshStatus} runTask={runTask} notify={notify} onEdit={() => openEditor(selected)} /> : <Surface title="方案详情"><EmptyState>选择一个方案查看路径、出口机、客户端和恢复状态。</EmptyState></Surface>}</div>
    </div>
    <ProfileEditor open={editorOpen} profile={editorProfile} fleet={fleet} refreshFleet={refreshFleet} onClose={() => setEditorOpen(false)} onSaved={onSaved} runTask={runTask} notify={notify} />
  </>;
}
