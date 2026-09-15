export interface SessionInfo {
  authenticated: boolean;
  csrf: string | null;
  passkeys: boolean;
  secure: boolean;
  version: string;
}

export interface Credential {
  id: string;
  name: string;
  kind: 'key' | 'password';
}

export interface Host {
  id: string;
  name: string;
  address: string;
  port: number;
  username: string;
  credential_id: string;
  jump_id: string;
  host_key: string;
  group: string;
  favorite: boolean;
  terminal_enabled: boolean;
  codex_enabled: boolean;
  browser_enabled: boolean;
  codex_workspace: string;
}

export interface NetworkProfile {
  cleanup_pending?: boolean;
  proxy_mode?: 'direct' | 'share';
  proxy_host?: string;
  proxy_port?: number;
  id: string;
  name: string;
  gateway_id: string;
  client_ids: string[];
  port: number;
  endpoint: string;
  tunnel_cidr: string;
  preserve_routes: string[];
  maintenance: boolean;
  state: 'disabled' | 'enabling' | 'enabled' | 'disabling' | 'error';
  updated_at: number;
  interface: string;
  last_error: string;
}

export interface ProbeResult {
  id: string;
  name: string;
  address: string;
  ssh: boolean;
  dns: boolean;
  internet: boolean;
  helper: boolean;
  hostname: string;
  os: string;
  default_route: string;
  http_code: string;
  system_internet?: boolean | null;
  proxy_enabled?: boolean;
  diagnosis?: string;
  client_supported?: boolean;
  gateway_supported?: boolean;
  codex?: boolean;
  codex_version?: string;
  browser?: boolean;
  browser_name?: string;
  error: string;
}

export interface ClashStatus {
  installed: boolean;
  running: boolean;
  controller: boolean;
  version?: string;
  config_path?: string;
  mode: string;
  mixed_port: number;
  tun: boolean;
  system_proxy?: { supported: boolean; enabled: boolean | null; server?: string; reason?: string };
  rules?: Array<{ type?: string; payload?: string; proxy?: string }>;
  policies?: string[];
  groups?: Array<{ name: string; type: string; now: string; all: string[] }>;
  backup?: string;
  error?: string;
}

export interface CodexMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  status: 'running' | 'done' | 'error';
  created_at: number;
}

export interface CodexSession {
  id: string;
  host_id: string;
  title: string;
  workspace: string;
  sandbox: 'read-only' | 'workspace-write';
  remote_thread_id: string;
  status: 'idle' | 'running' | 'done' | 'error';
  last_error: string;
  created_at: number;
  updated_at: number;
  model: string;
  reasoning_effort: string;
  service_tier: string;
  messages?: CodexMessage[];
}

export interface CodexModel {
  id: string;
  displayName: string;
  description: string;
  isDefault: boolean;
  defaultReasoningEffort: string;
  supportedReasoningEfforts: Array<{ reasoningEffort: string; description: string }>;
  serviceTiers: Array<{ id: string; name: string; description: string }>;
}

export interface CodexRemoteThread {
  id: string;
  name?: string;
  preview?: string;
  cwd?: string;
  model?: string;
  reasoningEffort?: string;
  source?: string;
  updatedAt: number;
  turns?: Array<{ items?: Array<Record<string, unknown>> }>;
}

export interface CodexProject {
  path: string;
  count: number;
  updated_at: number;
}

export interface AuditEvent {
  id: number;
  created_at: number;
  actor: string;
  action: string;
  target: string;
  details: Record<string, unknown>;
  remote_ip: string;
}

export interface SecurityInfo {
  devices: Array<{
    token_hash: string;
    user_agent: string;
    remote_ip: string;
    created_at: number;
    expires_at: number;
    current: boolean;
  }>;
  passkeys: Array<{ id: string; name: string; created_at: number }>;
  key_enabled: boolean;
  verified: boolean;
}
