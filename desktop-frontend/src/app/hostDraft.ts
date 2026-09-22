import type { HostDraft, HostRecord } from '../api/types';

export const emptyHost: HostDraft = {
  id: '', name: '', address: '', username: '', port: 22, credential_id: '', jump_id: '', host_key: '', group: '',
  favorite: false, terminal_enabled: true, codex_enabled: true, browser_enabled: true, codex_workspace: '',
};

export function hostDraftFrom(host?: HostRecord): HostDraft {
  if (!host) return emptyHost;
  return {
    id: host.id,
    name: host.name,
    address: host.address,
    username: host.username,
    port: host.port,
    credential_id: host.credential_id,
    jump_id: host.jump_id || '',
    host_key: host.host_key || '',
    group: host.group || '',
    favorite: host.favorite ?? false,
    terminal_enabled: host.terminal_enabled ?? true,
    codex_enabled: host.codex_enabled ?? true,
    browser_enabled: host.browser_enabled ?? true,
    codex_workspace: host.codex_workspace || '',
  };
}
