import { useCallback, useEffect, useRef, useState } from 'react';
import { Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Button, FluentProvider } from '@fluentui/react-components';
import { Dismiss24Regular } from '@fluentui/react-icons';
import { getDiagnostics } from '../api/diagnostics';
import { getCredentials, getHosts, getNetwork, getProxy } from '../api';
import { getUpdates } from '../api/updates';
import { getStatus } from '../api/status';
import type { DiagnosticResponse, FleetState, ProxyResponse, StatusResponse, UpdateResponse } from '../api/types';
import { operationsDarkTheme, operationsLightTheme } from '../theme/tokens';
import type { Confirmation, DataFreshness, RunTask, SectionId, ThemeMode } from './types';
import { Shell } from './Shell';
import { OverviewPage } from '../pages/OverviewPage';
import { TunnelsPage } from '../pages/TunnelsPage';
import { SharingPage } from '../pages/SharingPage';
import { HostsPage } from '../pages/HostsPage';
import { ProxyPage } from '../pages/ProxyPage';
import { DiagnosticsPage } from '../pages/DiagnosticsPage';
import { SettingsPage } from '../pages/SettingsPage';

function initialThemeMode(): ThemeMode {
  try {
    const value = window.localStorage.getItem('desktop-theme');
    return value === 'dark' || value === 'light' || value === 'system' ? value : 'system';
  } catch {
    return 'system';
  }
}

function isDarkMode(mode: ThemeMode): boolean {
  if (mode === 'dark') return true;
  if (mode === 'light') return false;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches === true;
}

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请查看诊断与恢复页面。';
}

export function App() {
  const [section, setSection] = useState<SectionId>('overview');
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [fleet, setFleet] = useState<FleetState>({hosts: [], credentials: [], profiles: []});
  const [fleetFreshness, setFleetFreshness] = useState<DataFreshness>('unknown');
  const [proxy, setProxy] = useState<ProxyResponse | null>(null);
  const [proxyFreshness, setProxyFreshness] = useState<DataFreshness>('unknown');
  const [diagnostics, setDiagnostics] = useState<DiagnosticResponse | null>(null);
  const [updates, setUpdates] = useState<UpdateResponse | null>(null);
  const [statusFreshness, setStatusFreshness] = useState<DataFreshness>('unknown');
  const [themeMode, setThemeMode] = useState<ThemeMode>(initialThemeMode);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const confirmationResolver = useRef<((value: boolean) => void) | null>(null);
  const busyRef = useRef(false);
  const statusGenerationRef = useRef(0);
  const appliedStatusGenerationRef = useRef(0);
  const statusRequestRef = useRef<{generation: number; promise: Promise<StatusResponse>} | null>(null);
  const forcedStatusRequestRef = useRef<Promise<StatusResponse> | null>(null);

  const notify = useCallback((message: string) => setNotice(message), []);

  const refreshStatus = useCallback((force = false): Promise<StatusResponse> => {
    const current = statusRequestRef.current;
    if (!force && current) return current.promise;
    if (force && forcedStatusRequestRef.current) return forcedStatusRequestRef.current;

    const startRequest = () => {
      const generation = ++statusGenerationRef.current;
      const promise = getStatus().then(result => {
        if (generation >= appliedStatusGenerationRef.current) {
          appliedStatusGenerationRef.current = generation;
          setStatus(result);
          setStatusFreshness('fresh');
        }
        return result;
      }).catch(error => {
        if (generation >= appliedStatusGenerationRef.current) setStatusFreshness('unknown');
        throw error;
      }).finally(() => {
        if (statusRequestRef.current?.generation === generation) statusRequestRef.current = null;
      });
      statusRequestRef.current = {generation, promise};
      return promise;
    };

    if (!force) return startRequest();
    const previous = statusRequestRef.current?.promise;
    const forced = (async () => {
      if (previous) await previous.catch(() => undefined);
      if (statusRequestRef.current && statusRequestRef.current.promise !== previous) {
        await statusRequestRef.current.promise.catch(() => undefined);
      }
      return startRequest();
    })();
    forcedStatusRequestRef.current = forced;
    forced.then(() => {
      if (forcedStatusRequestRef.current === forced) forcedStatusRequestRef.current = null;
    }, () => {
      if (forcedStatusRequestRef.current === forced) forcedStatusRequestRef.current = null;
    });
    return forced;
  }, []);

  const refreshFleet = useCallback(async () => {
    try {
      const [hosts, credentials, network] = await Promise.all([getHosts(), getCredentials(), getNetwork()]);
      const result = {hosts: hosts.hosts || [], credentials: credentials.credentials || [], profiles: network.profiles || []};
      setFleet(result);
      setFleetFreshness('fresh');
      return result;
    } catch (error) {
      setFleetFreshness('unknown');
      throw error;
    }
  }, []);

  const loadProxy = useCallback(async () => {
    try {
      const result = await getProxy();
      setProxy(result);
      setProxyFreshness('fresh');
      return result;
    } catch (error) {
      setProxyFreshness('unknown');
      throw error;
    }
  }, []);

  const loadDiagnostics = useCallback(async () => {
    const result = await getDiagnostics();
    setDiagnostics(result);
    return result;
  }, []);

  const askConfirmation = useCallback((value: Confirmation) => new Promise<boolean>(resolve => {
    confirmationResolver.current = resolve;
    setConfirmation(value);
  }), []);

  const finishConfirmation = (answer: boolean) => {
    const resolve = confirmationResolver.current;
    confirmationResolver.current = null;
    setConfirmation(null);
    resolve?.(answer);
  };

  const runTask: RunTask = useCallback(async (operation, confirm, sync): Promise<boolean> => {
    if (busyRef.current) {
      setNotice('已有操作执行中，请等待结果。');
      return false;
    }
    busyRef.current = true;
    setBusy(true);
    try {
      if (confirm && !await askConfirmation(confirm)) return false;
      setNotice('正在执行，请等待结果；远端操作可能需要数分钟。');
      let operationError: unknown;
      try {
        await operation();
      } catch (error) {
        operationError = error;
        throw error;
      } finally {
        if (sync) {
          try {
            await sync();
          } catch (syncError) {
            if (!operationError) throw syncError;
          }
        }
      }
      return true;
    } catch (error) {
      setNotice(messageFrom(error));
      return false;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [askConfirmation]);

  useEffect(() => {
    document.body.classList.toggle('sna-dark', isDarkMode(themeMode));
    try { window.localStorage.setItem('desktop-theme', themeMode); } catch { /* optional local preference */ }
  }, [themeMode]);

  useEffect(() => {
    void refreshStatus().catch(error => setNotice(messageFrom(error)));
    const interval = window.setInterval(() => {
      if (!document.hidden) void refreshStatus().catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(interval);
  }, [refreshStatus]);

  useEffect(() => {
    const loadForSection = async () => {
      try {
        if (section === 'sharing' || section === 'hosts-page' || section === 'diagnostics') await refreshFleet();
        if (section === 'proxy-page') await loadProxy();
        if (section === 'diagnostics') await loadDiagnostics();
        if (section === 'settings' && !updates) setUpdates(await getUpdates());
      } catch (error) {
        setNotice(messageFrom(error));
      }
    };
    void loadForSection();
  }, [section, refreshFleet, loadProxy, loadDiagnostics, updates]);

  const refreshCurrent = async () => {
    const success = await runTask(async () => {
      await refreshStatus(true);
      if (section === 'sharing' || section === 'hosts-page' || section === 'diagnostics') await refreshFleet();
      if (section === 'proxy-page') await loadProxy();
      if (section === 'diagnostics') await loadDiagnostics();
    });
    if (success) notify('状态已刷新。');
  };

  const renderPage = () => {
    if (section === 'overview') return <OverviewPage status={status} onNavigate={setSection} />;
    if (section === 'tunnel-page') return <TunnelsPage status={status} statusFreshness={statusFreshness} runTask={runTask} refreshStatus={refreshStatus} notify={notify} />;
    if (section === 'sharing') return <SharingPage fleet={fleet} fleetFreshness={fleetFreshness} refreshFleet={refreshFleet} refreshStatus={refreshStatus} runTask={runTask} notify={notify} />;
    if (section === 'hosts-page') return <HostsPage fleet={fleet} fleetFreshness={fleetFreshness} refreshFleet={refreshFleet} runTask={runTask} notify={notify} />;
    if (section === 'proxy-page') return <ProxyPage result={proxy} proxyFreshness={proxyFreshness} loadProxy={loadProxy} refreshStatus={refreshStatus} runTask={runTask} notify={notify} />;
    if (section === 'diagnostics') return <DiagnosticsPage status={status} statusFreshness={statusFreshness} fleet={fleet} fleetFreshness={fleetFreshness} diagnostics={diagnostics} loadDiagnostics={loadDiagnostics} refreshStatus={refreshStatus} refreshFleet={refreshFleet} runTask={runTask} notify={notify} />;
    return <SettingsPage status={status} updates={updates} notify={notify} runTask={runTask} />;
  };

  return <FluentProvider theme={isDarkMode(themeMode) ? operationsDarkTheme : operationsLightTheme}>
    <Shell currentSection={section} onNavigate={setSection} status={status} themeMode={themeMode} onThemeModeChange={setThemeMode} onRefresh={() => void refreshCurrent()} busy={busy}>
      {renderPage()}
    </Shell>
    {notice && <div id="notice" className="notice" role="status" aria-live="polite">{notice}</div>}
    <Dialog open={Boolean(confirmation)} modalType="alert" onOpenChange={(_, data) => {if (!data.open) finishConfirmation(false);}}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle action={<Button appearance="subtle" aria-label="取消" icon={<Dismiss24Regular />} onClick={() => finishConfirmation(false)} />}>{confirmation?.title || '确认操作'}</DialogTitle>
          <DialogContent><p id="confirm-text" style={{whiteSpace: 'pre-line'}}>{confirmation?.body || ''}</p></DialogContent>
          <DialogActions><Button id="cancel" appearance="secondary" onClick={() => finishConfirmation(false)}>取消</Button><Button id="accept" appearance="primary" onClick={() => finishConfirmation(true)}>继续</Button></DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  </FluentProvider>;
}
