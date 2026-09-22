import { useEffect, useState } from 'react';
import { Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Field, Input, Select, Tab, TabList, Textarea } from '@fluentui/react-components';
import { Add24Regular, Dismiss24Regular, Key24Regular, Search24Regular, Warning24Regular } from '@fluentui/react-icons';
import type { CredentialDraft, FleetState, HostDraft } from '../api/types';
import { deleteCredential, deleteHost, inspectHost, probeHosts, saveCredential, saveHost } from '../api/fleet';
import type { DataFreshness, RunTask } from '../app/types';
import { emptyHost, hostDraftFrom } from '../app/hostDraft';
import { probeSummary } from '../app/probes';
import { ButtonRow, EmptyState, formatTime, PageIntro, StatusPill, Surface } from '../app/ui';

interface HostsPageProps {
  fleet: FleetState;
  fleetFreshness: DataFreshness;
  refreshFleet: () => Promise<FleetState>;
  runTask: RunTask;
  notify: (message: string) => void;
}

const emptyCredential: CredentialDraft = {name: '', kind: 'password', secret: '', passphrase: ''};

function HostDialog({open, draft, credentials, hosts, onClose, onSaved, onProbe, runTask, notify}: {
  open: boolean;
  draft: HostDraft;
  credentials: FleetState['credentials'];
  hosts: FleetState['hosts'];
  onClose: () => void;
  onSaved: () => Promise<unknown>;
  onProbe: (hostId: string, result: NonNullable<Awaited<ReturnType<typeof probeHosts>>['results']>[number], checkedAt?: number) => void;
  runTask: RunTask;
  notify: HostsPageProps['notify'];
}) {
  const [value, setValue] = useState<HostDraft>(draft);
  const [fingerprintConfirmed, setFingerprintConfirmed] = useState(Boolean(draft.host_key));
  const [inspection, setInspection] = useState<{fingerprint?: string; host_key?: string} | null>(null);
  const [probeMessage, setProbeMessage] = useState('');

  useEffect(() => {
    setValue(draft);
    setFingerprintConfirmed(Boolean(draft.host_key));
    setInspection(null);
    setProbeMessage('');
  }, [draft]);

  const update = (key: keyof HostDraft, next: string | number) => {
    setValue(current => ({...current, [key]: next}));
    if (key === 'host_key') setFingerprintConfirmed(false);
  };

  const save = async () => {
    if (value.host_key && !fingerprintConfirmed) {
      notify('请先通过可信渠道核对 SSH 指纹 / 公钥，再勾选确认。');
      return;
    }
    if (!await runTask(async () => {
      await saveHost(value);
      onClose();
    }, undefined, onSaved)) return;
    notify('主机已保存。');
  };

  const inspect = async () => {
    if (!value.id) {
      notify('请先保存主机，再读取远端指纹。');
      return;
    }
    if (!await runTask(async () => {
      const result = await inspectHost(value.id);
      setInspection(result);
      setValue(current => ({...current, host_key: result.host_key || current.host_key}));
      setFingerprintConfirmed(false);
    })) return;
    notify('已读取远端公钥；读取不会自动信任，请对照可信渠道后再保存。');
  };

  const test = async () => {
    if (!value.id) {
      notify('请先保存主机，再进行连接探测。');
      return;
    }
    if (!await runTask(async () => {
      const result = await probeHosts([value.id]);
      const first = result.results?.[0];
      if (first) {
        onProbe(value.id, first, result.checked_at);
        setProbeMessage(probeSummary(first));
      } else setProbeMessage('后端未返回该主机的探测结果。');
    }, undefined, onSaved)) return;
    notify('主机探测完成。');
  };

  return <Dialog open={open} onOpenChange={(_, data) => {if (!data.open) onClose();}}>
    <DialogSurface>
      <DialogBody>
        <DialogTitle action={<Button appearance="subtle" aria-label="关闭" icon={<Dismiss24Regular />} onClick={onClose} />}>{value.id ? '编辑主机' : '新增主机'}</DialogTitle>
        <DialogContent>
          <div className="form-grid" id="host-fields">
            <Field label="名称" required><Input id="host-name" value={value.name} onChange={(_, data) => update('name', data.value)} /></Field>
            <Field label="地址" required><Input id="host-address" className="mono" value={value.address} onChange={(_, data) => update('address', data.value)} /></Field>
            <Field label="SSH 用户" required><Input id="host-username" value={value.username} onChange={(_, data) => update('username', data.value)} /></Field>
            <Field label="端口" required><Input id="host-port" type="number" value={String(value.port)} onChange={(_, data) => update('port', Number(data.value) || 0)} /></Field>
            <Field label="登录凭据" required><Select id="host-credential_id" value={value.credential_id} onChange={event => update('credential_id', event.target.value)}>
              <option value="">请选择凭据</option>
              {credentials.map(credential => <option key={credential.id} value={credential.id}>{credential.name} · {credential.kind === 'key' ? '私钥' : '密码'}</option>)}
            </Select></Field>
            <Field label="跳板（可选）"><Select id="host-jump_id" value={value.jump_id} onChange={event => update('jump_id', event.target.value)}>
              <option value="">不使用跳板机</option>
              {hosts.filter(host => host.id !== value.id).map(host => <option key={host.id} value={host.id}>{host.name} · {host.address}</option>)}
            </Select></Field>
            <Field className="span-2" label="SSH 公钥 / 指纹（保存前必须核对）" validationState={value.host_key && !fingerprintConfirmed ? 'warning' : undefined}>
              <Textarea id="host-host_key" className="mono" value={value.host_key} onChange={(_, data) => update('host_key', data.value)} resize="vertical" />
            </Field>
          </div>
          <div className={`danger-zone ${value.host_key && !fingerprintConfirmed ? '' : 'fingerprint-safe'}`} style={{marginTop: 16}}>
            <h4><Warning24Regular /> SSH 身份确认</h4>
            <p id="fingerprint">{inspection?.fingerprint ? `待核对指纹：${inspection.fingerprint}` : value.host_key ? '已保存公钥；仍应确认它与可信来源一致。' : '尚未固定 SSH 公钥。未确认的主机不能显示为可信。'}</p>
            <Checkbox id="fingerprint-confirmed" checked={fingerprintConfirmed} disabled={!value.host_key} onChange={(_, data) => setFingerprintConfirmed(Boolean(data.checked))} label={value.host_key ? '我已通过可信渠道核对以上 SSH 公钥 / 指纹。' : '保存公钥后才能确认 SSH 身份。'} />
            <div className="form-actions">
              <Button id="inspect-host" appearance="subtle" icon={<Search24Regular />} disabled={!value.id} onClick={() => void inspect()}>读取远端指纹</Button>
              <Button id="test-host" appearance="subtle" disabled={!value.id} onClick={() => void test()}>测试连接</Button>
            </div>
            <p id="fingerprint-status" className="muted">{fingerprintConfirmed ? '已确认 SSH 指纹 / 公钥' : value.host_key ? '待确认 SSH 指纹 / 公钥' : '未固定 SSH 指纹'}</p>
            {probeMessage && <p className="muted">{probeMessage}</p>}
          </div>
          <DialogActions>
            <Button appearance="secondary" onClick={onClose}>取消</Button>
            <Button appearance="primary" onClick={() => void save()} disabled={!value.name || !value.address || !value.username || !value.credential_id}>保存主机</Button>
          </DialogActions>
        </DialogContent>
      </DialogBody>
    </DialogSurface>
  </Dialog>;
}

function CredentialDialog({open, onClose, onSaved, runTask, notify}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => Promise<unknown>;
  runTask: HostsPageProps['runTask'];
  notify: HostsPageProps['notify'];
}) {
  const [draft, setDraft] = useState<CredentialDraft>(emptyCredential);

  const submit = async () => {
    const secret = draft.secret;
    setDraft(current => ({...current, secret: '', passphrase: ''}));
    if (!await runTask(async () => {
      await saveCredential({...draft, secret});
      await onSaved();
      onClose();
    })) return;
    notify('凭据已加密保存；输入内容已清空。');
  };

  return <Dialog open={open} onOpenChange={(_, data) => {if (!data.open) onClose();}}>
    <DialogSurface>
      <DialogBody>
        <DialogTitle action={<Button appearance="subtle" aria-label="关闭" icon={<Dismiss24Regular />} onClick={onClose} />}>新增凭据</DialogTitle>
        <DialogContent>
          <p className="muted">这里只显示名称和类型。密码、私钥与口令不会在保存后重新显示。</p>
          <div className="form-grid">
            <Field label="名称" required><Input id="credential-name" value={draft.name} onChange={(_, data) => setDraft(current => ({...current, name: data.value}))} /></Field>
            <Field label="类型" required><Select id="credential-kind" value={draft.kind} onChange={event => setDraft(current => ({...current, kind: event.target.value as CredentialDraft['kind']}))}><option value="password">密码</option><option value="key">私钥</option></Select></Field>
            <Field className="span-2" label={draft.kind === 'key' ? '私钥内容（仅本次提交）' : '密码（仅本次提交）'} required><Textarea id="credential-secret" className="mono" value={draft.secret} onChange={(_, data) => setDraft(current => ({...current, secret: data.value}))} resize="vertical" /></Field>
            {draft.kind === 'key' && <Field className="span-2" label="私钥口令（可选，仅本次提交）"><Input id="credential-passphrase" type="password" value={draft.passphrase} onChange={(_, data) => setDraft(current => ({...current, passphrase: data.value}))} /></Field>}
          </div>
          <DialogActions>
            <Button appearance="secondary" onClick={onClose}>取消</Button>
            <Button appearance="primary" disabled={!draft.name || !draft.secret} onClick={() => void submit()}>加密保存</Button>
          </DialogActions>
        </DialogContent>
      </DialogBody>
    </DialogSurface>
  </Dialog>;
}

export function HostsPage({fleet, fleetFreshness, refreshFleet, runTask, notify}: HostsPageProps) {
  const [tab, setTab] = useState<'hosts' | 'credentials'>('hosts');
  const [hostDialogOpen, setHostDialogOpen] = useState(false);
  const [credentialDialogOpen, setCredentialDialogOpen] = useState(false);
  const [hostDraft, setHostDraft] = useState<HostDraft>(emptyHost);
  const [probeCache, setProbeCache] = useState<Record<string, {result: NonNullable<Awaited<ReturnType<typeof probeHosts>>['results']>[number]; checkedAt?: number}>>({});

  const credentialUseCount = (id: string) => fleet.hosts.filter(host => host.credential_id === id).length;
  const hostName = (id?: string) => fleet.hosts.find(host => host.id === id)?.name || id || '未选择';

  const editHost = (hostId?: string) => {
    const host = fleet.hosts.find(item => item.id === hostId);
    setHostDraft(hostDraftFrom(host));
    setHostDialogOpen(true);
  };

  const removeHost = (id: string, name: string) => void runTask(async () => {
    await deleteHost(id);
    notify('主机已删除。');
  }, {title: '删除主机？', body: `将删除 ${name}。若仍被跳板或共享方案引用，后台会拒绝。`}, refreshFleet);

  const removeCredential = (id: string, name: string) => void runTask(async () => {
    await deleteCredential(id);
    notify('凭据已删除。');
  }, {title: '删除凭据？', body: `将删除 ${name}。仍被主机使用时后台会拒绝。`}, refreshFleet);

  const probeLevel = (result: NonNullable<Awaited<ReturnType<typeof probeHosts>>['results']>[number]) => {
    if (result.ssh === true && result.dns === true && !result.error) return 'ok' as const;
    if (result.ssh === false || result.error) return 'danger' as const;
    return 'warning' as const;
  };

  return <>
    <PageIntro title="主机与凭据" description="主机身份和登录凭据分开管理。表格只展示可用于运维判断的元数据，绝不重新显示密码、私钥或私钥口令。" />
    <Surface>
      <TabList id="host-tabs" selectedValue={tab} onTabSelect={(_, data) => setTab(data.value as 'hosts' | 'credentials')}>
        <Tab id="hosts-tab" value="hosts">Hosts · {fleet.hosts.length}</Tab>
        <Tab id="credentials-tab" value="credentials">Credentials · {fleet.credentials.length}</Tab>
      </TabList>
    </Surface>
    {tab === 'hosts' ? <Surface title="Hosts" description="SSH 指纹未确认时使用 Warning 状态，不能被绿色可信状态掩盖。" action={<Button appearance="primary" icon={<Add24Regular />} onClick={() => editHost()} disabled={fleetFreshness !== 'fresh'}>新增主机</Button>}>
      <div id="hosts" className="table-wrap">
        {!fleet.hosts.length ? <EmptyState>尚未添加主机。先添加凭据，再建立主机连接信息。</EmptyState> : <table className="data-table"><thead><tr><th>名称</th><th>地址</th><th>用户 / 端口</th><th>跳板</th><th>指纹状态</th><th>最近测试</th><th>状态</th><th>操作</th></tr></thead><tbody>
          {fleet.hosts.map(host => <tr key={host.id}>
            <td><strong>{host.name}</strong><div className="muted mono">{host.id}</div></td>
            <td className="mono">{host.address}</td>
            <td>{host.username}<div className="muted mono">:{host.port}</div></td>
            <td>{host.jump_id ? hostName(host.jump_id) : '—'}</td>
            <td><StatusPill level={host.host_key ? 'ok' : 'warning'}>{host.host_key ? '已确认' : '待确认'}</StatusPill></td>
            <td>{probeCache[host.id] ? <><StatusPill level={probeLevel(probeCache[host.id].result)}>{probeSummary(probeCache[host.id].result)}</StatusPill><div className="muted">{formatTime(probeCache[host.id].checkedAt)}</div></> : <span className="muted">本次会话未测试</span>}</td>
            <td><StatusPill level="info">已保存</StatusPill></td>
            <td><ButtonRow><Button size="small" appearance="subtle" onClick={() => editHost(host.id)} disabled={fleetFreshness !== 'fresh'}>编辑</Button><Button size="small" appearance="subtle" onClick={() => void removeHost(host.id, host.name)} disabled={fleetFreshness !== 'fresh'}>删除</Button></ButtonRow></td>
          </tr>)}
        </tbody></table>}
      </div>
    </Surface> : <Surface title="Credentials" description="只显示名称、类型和关联主机数；秘密材料不会重新读取。" action={<Button appearance="primary" icon={<Key24Regular />} onClick={() => setCredentialDialogOpen(true)}>新增凭据</Button>}>
      <div id="credentials" className="table-wrap">
        {!fleet.credentials.length ? <EmptyState>尚未保存凭据。</EmptyState> : <table className="data-table"><thead><tr><th>名称</th><th>类型</th><th>关联主机数</th><th>操作</th></tr></thead><tbody>
          {fleet.credentials.map(credential => <tr key={credential.id}><td><strong>{credential.name}</strong><div className="muted mono">{credential.id}</div></td><td>{credential.kind === 'key' ? '私钥' : '密码'}</td><td>{credentialUseCount(credential.id)}</td><td><Button size="small" appearance="subtle" onClick={() => void removeCredential(credential.id, credential.name)} disabled={fleetFreshness !== 'fresh'}>删除</Button></td></tr>)}
        </tbody></table>}
      </div>
    </Surface>}
    <HostDialog key={`${hostDraft.id || 'new'}-${hostDialogOpen ? 'open' : 'closed'}`} open={hostDialogOpen} draft={hostDraft} credentials={fleet.credentials} hosts={fleet.hosts} onClose={() => setHostDialogOpen(false)} onSaved={refreshFleet} onProbe={(hostId, result, checkedAt) => setProbeCache(current => ({...current, [hostId]: {result, checkedAt}}))} runTask={runTask} notify={notify} />
    <CredentialDialog open={credentialDialogOpen} onClose={() => setCredentialDialogOpen(false)} onSaved={refreshFleet} runTask={runTask} notify={notify} />
  </>;
}
