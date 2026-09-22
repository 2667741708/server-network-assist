import { useEffect, useState } from 'react';
import { Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Field, Input, Textarea } from '@fluentui/react-components';
import { ArrowClockwise24Regular, Dismiss24Regular, History24Regular, Save24Regular } from '@fluentui/react-icons';
import type { ProxyResponse, ProxySummary } from '../api/types';
import { restoreProxy, saveProxy } from '../api/proxy';
import { runTunnelAction } from '../api/status';
import type { DataFreshness, RunTask } from '../app/types';
import { ButtonRow, EmptyState, formatTime, PageIntro, StatusPill, Surface, unknown } from '../app/ui';

interface ProxyPageProps {
  result: ProxyResponse | null;
  proxyFreshness: DataFreshness;
  loadProxy: () => Promise<ProxyResponse>;
  refreshStatus: (force?: boolean) => Promise<unknown>;
  runTask: RunTask;
  notify: (message: string) => void;
}

function proxyLevel(proxy: ProxySummary | undefined): 'ok' | 'warning' | 'danger' | 'info' {
  if (!proxy || proxy.supported === false || proxy.enabled == null) return 'danger';
  if (proxy.enabled || proxy.pac) return 'warning';
  return 'ok';
}

function proxyLabel(proxy: ProxySummary | undefined): string {
  if (!proxy || proxy.supported === false || proxy.enabled == null) return '无法读取';
  return proxy.enabled ? '手动代理已开启' : proxy.pac ? 'PAC 生效，手动代理已关闭' : '手动代理已关闭';
}

function ProxyEditor({open, proxy, onClose, onSaved, reload, refreshStatus, runTask, notify}: {
  open: boolean;
  proxy: ProxySummary | undefined;
  onClose: () => void;
  onSaved: (result: ProxyResponse) => void;
  reload: () => Promise<ProxyResponse>;
  refreshStatus: ProxyPageProps['refreshStatus'];
  runTask: RunTask;
  notify: ProxyPageProps['notify'];
}) {
  const [enabled, setEnabled] = useState(Boolean(proxy?.enabled));
  const [server, setServer] = useState(proxy?.server || '');
  const [bypass, setBypass] = useState(Array.isArray(proxy?.bypass) ? proxy?.bypass.join(';') : proxy?.bypass || '');

  useEffect(() => {
    setEnabled(Boolean(proxy?.enabled));
    setServer(proxy?.server || '');
    setBypass(Array.isArray(proxy?.bypass) ? proxy?.bypass.join(';') : proxy?.bypass || '');
  }, [proxy]);

  const submit = async () => {
    if (!await runTask(async () => {
      const result = await saveProxy({enabled, server, bypass});
      onSaved(result);
      onClose();
    }, {title: '保存当前用户代理？', body: '将先备份当前配置，再应用编辑后的代理。错误的地址会影响使用系统代理的应用。'}, async () => {
      await Promise.all([reload(), refreshStatus(true)]);
    })) return;
    notify('已备份原配置并保存代理，请检查系统应用联网结果。');
  };

  return <Dialog open={open} onOpenChange={(_, data) => {if (!data.open) onClose();}}>
    <DialogSurface>
      <DialogBody>
        <DialogTitle action={<Button appearance="subtle" aria-label="关闭" icon={<Dismiss24Regular />} onClick={onClose} />}>备份并修改当前用户代理</DialogTitle>
        <DialogContent>
          <div className="form-grid">
            <div className="span-2"><Checkbox id="proxy-enabled" checked={enabled} onChange={(_, data) => setEnabled(Boolean(data.checked))} label="启用当前用户手动代理" /></div>
            <Field className="span-2" label="代理地址"><Input id="proxy-server" className="mono" value={server} onChange={(_, data) => setServer(data.value)} placeholder="例如 127.0.0.1:7897" /></Field>
            <Field className="span-2" label="绕过地址"><Textarea id="proxy-bypass" className="mono" value={bypass} onChange={(_, data) => setBypass(data.value)} placeholder="Windows 用分号分隔；Ubuntu 使用逗号或分号" resize="vertical" /></Field>
          </div>
          <p className="muted">只修改当前用户的代理设置，不修改 WinHTTP、其他用户、终端环境变量或系统服务。关闭手动代理不会移除 PAC。</p>
          <DialogActions>
            <Button appearance="secondary" onClick={onClose}>取消</Button>
            <Button appearance="primary" icon={<Save24Regular />} onClick={() => void submit()}>备份并保存</Button>
          </DialogActions>
        </DialogContent>
      </DialogBody>
    </DialogSurface>
  </Dialog>;
}

export function ProxyPage({result, proxyFreshness, loadProxy, refreshStatus, runTask, notify}: ProxyPageProps) {
  const [editorOpen, setEditorOpen] = useState(false);
  const [current, setCurrent] = useState<ProxyResponse | null>(result);
  const proxy = current?.proxy;

  useEffect(() => setCurrent(result), [result]);

  const reload = async () => {
    if (!await runTask(async () => setCurrent(await loadProxy()))) return;
    notify('代理和备份已重新读取。');
  };

  const disable = async () => {
    if (!await runTask(async () => {
      await runTunnelAction('disable-proxy');
    }, {title: '关闭手动代理？', body: '将备份当前配置，再关闭手动代理。自动代理 PAC 或代理软件重新接管的设置需要单独检查。'}, async () => {
      await Promise.all([loadProxy(), refreshStatus(true)]);
    })) return;
    notify('手动代理已关闭，原配置已备份。');
  };

  const restore = (id: string) => void runTask(async () => {
    setCurrent(await restoreProxy(id));
  }, {title: '恢复代理配置？', body: `将恢复备份 ${id}，并先备份当前设置。恢复后请重新读取状态。`}, async () => {
    await Promise.all([loadProxy(), refreshStatus(true)]);
  }).then(success => {if (success) notify('代理备份已恢复。');});

  const manualText = proxy?.supported === false || proxy?.enabled == null ? '未知' : proxy.enabled ? '已开启' : '已关闭';
  const pacText = proxy?.supported === false || proxy?.enabled == null ? '未知' : proxy.pac ? '存在 PAC' : '未设置 PAC';

  return <>
    <PageIntro title="系统代理" description="先读取当前用户代理，再决定是否修改。每次写入和恢复都会保留可恢复的备份。" action={<Button appearance="subtle" icon={<ArrowClockwise24Regular />} onClick={() => void reload()}>重新读取</Button>} />
    <Surface title="当前用户代理状态" description={proxy?.scope || '范围由后台返回；不扩大到其他用户或系统服务。'} action={<Button id="save-proxy" appearance="primary" onClick={() => setEditorOpen(true)} disabled={proxyFreshness !== 'fresh' || proxy?.supported === false}>备份并修改</Button>}>
      <div id="proxy-state" className="status-hero" data-state={proxyLevel(proxy)} style={{padding: 18}}>
        <div className="status-hero-main">
          <StatusPill level={proxyLevel(proxy)}>{proxyLabel(proxy)}</StatusPill>
          <p id="proxy-description">{proxy?.scope || '代理设置尚未读取。'}{proxy?.pac ? ' 关闭手动代理不会移除 PAC。' : ''}{proxy?.error ? ` ${proxy.error}` : ''}</p>
        </div>
        <div className="status-hero-aside">
          <span>代理地址</span><strong id="proxy-address" className="mono">{proxy?.server || '未设置'}</strong>
        </div>
      </div>
      <dl className="definition-grid" style={{marginTop: 18}}>
        <div><dt>手动代理</dt><dd id="proxy-manual-state">{manualText}</dd></div>
        <div><dt>PAC</dt><dd id="proxy-pac-state">{pacText}</dd></div>
        <div><dt>影响范围</dt><dd>{unknown(proxy?.scope)}</dd></div>
        <div><dt>安全说明</dt><dd>关闭手动代理不会移除 PAC。</dd></div>
      </dl>
      <div className="form-actions">
        <Button id="load-proxy" appearance="subtle" onClick={() => void reload()}>重新读取</Button>
        <Button id="disable-proxy" appearance="outline" disabled={proxyFreshness !== 'fresh' || proxy?.supported === false || !proxy?.enabled} onClick={() => void disable()}>备份并关闭</Button>
      </div>
    </Surface>
    <Surface title="代理备份历史" description="恢复操作需要确认；不兼容当前系统的备份不会被启用。">
      <div id="proxy-backups" className="table-wrap">
        {!current?.backups?.length ? <EmptyState>还没有代理配置备份。</EmptyState> : <table className="data-table"><thead><tr><th>时间</th><th>标识</th><th>平台</th><th>状态摘要</th><th>操作</th></tr></thead><tbody>
          {current.backups.map(backup => <tr key={backup.id}><td>{formatTime(backup.created_at)}</td><td className="mono">{backup.id}</td><td>{backup.platform || '未知'}</td><td><StatusPill level={backup.compatible === false ? 'warning' : 'ok'}>{backup.compatible === false ? '不兼容当前系统' : '可恢复'}</StatusPill></td><td><ButtonRow><Button size="small" appearance="subtle" icon={<History24Regular />} disabled={proxyFreshness !== 'fresh' || backup.compatible === false} onClick={() => restore(backup.id)}>恢复</Button></ButtonRow></td></tr>)}
        </tbody></table>}
      </div>
    </Surface>
    <ProxyEditor open={editorOpen} proxy={proxy} onClose={() => setEditorOpen(false)} onSaved={setCurrent} reload={loadProxy} refreshStatus={refreshStatus} runTask={runTask} notify={notify} />
  </>;
}
