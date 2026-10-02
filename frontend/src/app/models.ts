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
  runtime?: { status: string; mismatch: boolean; nodes: Array<{ host_id: string; status: string; checked_at: number; external_tunnels: string[]; last_error: string }> };
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
  checked_at?: number;
  public_route?: string;
  return_route?: string;
  global_tunnels?: string[];
  route_warnings?: string[];
  assist?: Array<{ profile_id: string; interface: string; active: boolean; desired: boolean; suspended?: boolean; last_error?: string }>;
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

export type CommercialSection =
  | 'overview'
  | 'customers'
  | 'subscriptions'
  | 'devices'
  | 'nodes'
  | 'leases'
  | 'directory';

export interface CommercialPlan {
  id: string;
  name: string;
  download_bps: number | null;
  upload_bps: number | null;
  quota_bytes: number | null;
  period_seconds: number;
  max_devices: number;
  lease_seconds: number;
  enabled: boolean | number;
  created_at: number;
}

export interface CommercialCustomer {
  id: string;
  display_name: string;
  plan_id: string;
  enabled: boolean | number;
  revoked_at: number | null;
  created_at: number;
  lifecycle?: 'active' | 'archived' | 'deleted';
  tags?: string[];
  notes?: string;
}

export interface CommercialSource {
  id: string;
  name: string;
  endpoint: string;
  relay_public_key: string;
  address_pool: string;
  relay_interface: string;
  egress_interface: string;
  egress_mode?: 'physical' | 'source_proxy';
  egress_gateway?: string;
  proxy_interface?: string;
  dns?: string;
  enabled?: boolean;
  archived?: boolean;
  revision?: string;
  [key: string]: unknown;
}

export interface CommercialDevice {
  id: string;
  customer_id: string;
  label: string;
  public_key: string;
  wireguard_public_key: string;
  enabled: boolean | number;
  revoked_at: number | null;
  created_at: number;
  last_seen_at: number | null;
}

export interface CommercialGrant {
  id: string;
  customer_id: string;
  alias: string;
  tunnel: string;
  endpoint: string;
  enabled: boolean | number;
  relay_public_key: string;
  allocated_address: string;
  dns: string;
  allowed_ips: string;
  mtu: number;
  relay_interface: string;
  egress_interface: string;
  egress_policy: string;
  expires_at: number | null;
  created_at: number;
}

export interface CommercialLease {
  id: string;
  customer_id: string;
  device_id: string;
  grant_id: string;
  issued_at: number;
  expires_at: number;
  revoked_at: number | null;
  last_rx: number;
  last_tx: number;
}

export interface SubscriptionAddressStatus {
  customer_id: string;
  expires_at: number | null;
  used_at: number | null;
  created_at: number;
  available: boolean;
  can_reissue: boolean;
  status: string;
}

export interface CommercialSnapshot {
  sources: CommercialSource[];
  plans: CommercialPlan[];
  customers: CommercialCustomer[];
  subscription_addresses: SubscriptionAddressStatus[];
  devices: CommercialDevice[];
  grants: CommercialGrant[];
  leases: CommercialLease[];
  source_management_supported?: boolean;
  source_proxy?: Record<string, unknown>;
  physical_defaults?: Record<string, string>;
  local_proxy_source_ids?: string[];
}

export interface DashboardCustomer extends CommercialCustomer {
  plan: CommercialPlan;
  usage: {
    used_bytes: number;
    remaining_bytes: number | null;
    quota_bytes: number | null;
    today_bytes?: number;
    total_bytes?: number;
    measurement_status?: string;
    last_report_at?: number | null;
  };
  grants: Array<{
    id: string;
    name: string;
    source_id: string | null;
    source_name: string;
    endpoint: string;
    enabled: boolean;
    expires_at: number | null;
    egress_mode: string;
    available: boolean;
    reasons: string[];
  }>;
  devices: CommercialDevice[];
  leases: CommercialLease[];
  active_lease_count: number;
  usable: boolean;
  reasons: string[];
  version: number;
  revision: string;
}

export interface DashboardSnapshot {
  generated_at: number;
  customers: DashboardCustomer[];
  summary: {
    customers: number;
    usable: number;
    active_leases: number;
    today_bytes: number;
    total_bytes: number;
  };
  measurement_note?: string;
}
