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
  error: string;
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
  messages?: CodexMessage[];
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
