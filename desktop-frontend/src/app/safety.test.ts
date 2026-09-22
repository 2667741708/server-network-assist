import { describe, expect, it } from 'vitest';
import type { HostRecord, ProbeResult } from '../api/types';
import { hostDraftFrom } from './hostDraft';
import { probeCapabilities } from './probes';
import { sharingActions } from './sharingState';

describe('desktop safety state helpers', () => {
  it('preserves host capability flags and workspace when editing an otherwise unchanged host', () => {
    const host: HostRecord = {
      id: 'host-1', name: '旧名称', address: '192.0.2.10', username: 'operator', port: 22, credential_id: 'cred-1',
      favorite: true, terminal_enabled: false, codex_enabled: false, browser_enabled: true, codex_workspace: 'D:/ops/workspace',
    };
    const draft = hostDraftFrom(host);
    expect(draft).toMatchObject({favorite: true, terminal_enabled: false, codex_enabled: false, browser_enabled: true, codex_workspace: 'D:/ops/workspace'});
  });

  it('keeps transitional or cleanup-pending sharing profiles on recovery-only controls', () => {
    for (const state of ['enabling', 'disabling']) {
      expect(sharingActions({state, cleanup_pending: false}, 'fresh')).toMatchObject({enable: false, disable: true, delete: false, recovery: true});
    }
    expect(sharingActions({state: 'error', cleanup_pending: true}, 'fresh')).toMatchObject({enable: false, disable: true, delete: false, recovery: true});
    expect(sharingActions({state: 'disabled', cleanup_pending: false}, 'unknown')).toMatchObject({enable: false, disable: false, delete: false});
  });

  it('does not call a pre-enable client without public internet unavailable', () => {
    const probe: ProbeResult = {ssh: true, dns: true, internet: false, helper: true, client_supported: true, gateway_supported: false};
    const clientInternet = probeCapabilities(probe, 'client', 'disabled').find(item => item.key === 'internet');
    const gatewayInternet = probeCapabilities(probe, 'gateway', 'enabled').find(item => item.key === 'internet');
    expect(clientInternet).toMatchObject({detail: '启用共享前不判定客户端公网', level: 'info'});
    expect(gatewayInternet).toMatchObject({detail: 'Failed', level: 'danger'});
  });
});
