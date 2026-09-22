export type HealthLevel = 'ok' | 'warning' | 'danger' | 'info';

export interface ConnectivityResult {
  ok?: boolean | null;
  milliseconds?: number | null;
  scope?: string;
}

export interface RouteInfo {
  adapter?: string;
  gateway?: string;
  metric?: number | null;
}

export interface TunnelInfo {
  name: string;
  active: boolean;
  addresses?: string[];
  received?: number | null;
  sent?: number | null;
  handshake?: number | null;
  endpoint?: string;
  telemetry?: boolean;
  start_mode?: string;
  service_state?: string;
}

export interface RecoveryRecord {
  name: string;
  state?: string;
  start_mode?: string;
  was_active?: boolean;
  created_at?: number;
  delayed?: boolean;
}

export interface TrafficPoint {
  timestamp: number;
  received_per_second?: number | null;
  sent_per_second?: number | null;
}

export interface TrafficInfo {
  available?: boolean;
  scope?: string;
  received_per_second?: number | null;
  sent_per_second?: number | null;
  history?: TrafficPoint[];
}

export interface BackgroundInfo {
  running?: boolean;
  tray_available?: boolean;
  tray_running?: boolean;
  notifications_enabled?: boolean;
}

export interface ProxySummary {
  supported?: boolean;
  enabled?: boolean | null;
  server?: string;
  bypass?: string | string[];
  pac?: boolean;
  scope?: string;
  error?: string;
}

export interface StatusResponse {
  hostname?: string;
  platform?: string;
  version?: string;
  timestamp?: number;
  elevated?: boolean;
  direct?: ConnectivityResult;
  system?: ConnectivityResult;
  tunnels?: TunnelInfo[];
  routes?: RouteInfo[];
  traffic?: TrafficInfo;
  recovery?: RecoveryRecord[];
  background?: BackgroundInfo;
  proxy?: ProxySummary;
}

export interface HealthResponse {
  app?: string;
  pid?: number;
  version?: string;
}

export interface HostRecord {
  id: string;
  name: string;
  address: string;
  username: string;
  port: number;
  credential_id: string;
  jump_id?: string;
  host_key?: string;
  group?: string;
  favorite?: boolean;
  terminal_enabled?: boolean;
  codex_enabled?: boolean;
  browser_enabled?: boolean;
  codex_workspace?: string;
}

export interface CredentialRecord {
  id: string;
  name: string;
  kind: 'key' | 'password' | string;
}

export interface SharingProfile {
  id: string;
  name: string;
  gateway_id: string;
  client_ids: string[];
  port: number;
  endpoint: string;
  tunnel_cidr: string;
  preserve_routes: string[];
  maintenance: boolean;
  proxy_mode: 'direct' | 'share' | string;
  proxy_host: string;
  proxy_port: number;
  state: string;
  updated_at?: number;
  interface?: string;
  last_error?: string;
  cleanup_pending?: boolean;
}

export interface FleetHostsResponse {
  hosts?: HostRecord[];
}

export interface FleetCredentialsResponse {
  credentials?: CredentialRecord[];
}

export interface FleetNetworkResponse {
  profiles?: SharingProfile[];
}

export interface FleetAuditEvent {
  created_at?: number;
  action?: string;
  target?: string;
  details?: unknown;
}

export interface FleetAuditResponse {
  events?: FleetAuditEvent[];
}

export interface HostInspectResponse {
  host_key?: string;
  fingerprint?: string;
}

export interface ProbeResult {
  id?: string;
  name?: string;
  address?: string;
  ssh?: boolean | null;
  dns?: boolean | null;
  internet?: boolean | null;
  helper?: boolean | null;
  client_supported?: boolean | null;
  gateway_supported?: boolean | null;
  os?: string;
  hostname?: string;
  default_route?: string;
  http_code?: string;
  error?: string;
  checked_at?: number;
  [key: string]: unknown;
}

export interface ProbeResponse {
  results?: ProbeResult[];
  checked_at?: number;
}

export interface FleetActionResponse {
  ok?: boolean;
  id?: string;
  host?: HostRecord;
  profile?: SharingProfile;
  results?: ProbeResult[];
  recovery?: RecoveryRecord;
  scope?: string;
}

export interface ProxyBackup {
  id: string;
  created_at?: number;
  platform?: string;
  compatible?: boolean;
}

export interface ProxyResponse {
  proxy?: ProxySummary;
  backups?: ProxyBackup[];
  ok?: boolean;
  backup_id?: string;
}

export interface CampusStatus {
  configured?: boolean;
  state?: string;
  online?: boolean;
  account?: string;
  service?: string;
  ip?: string;
  internet_online?: boolean | null;
}

export interface CampusLoginResponse {
  ok?: boolean;
  verified?: boolean;
  current?: CampusStatus;
  message?: string;
}

export interface GuidanceItem {
  code?: string;
  severity?: 'error' | 'warning' | 'info' | string;
  title?: string;
  detail?: string;
}

export interface DiagnosticResponse {
  status?: StatusResponse | null;
  events?: LocalEvent[];
  guidance?: GuidanceItem[];
  error?: string | null;
}

export interface LocalEvent {
  timestamp?: number;
  action?: string;
  outcome?: string;
  detail?: unknown;
}

export interface UpdateAsset {
  name?: string;
  url?: string;
}

export interface UpdateResponse {
  current?: string;
  latest?: string;
  available?: boolean;
  release_url?: string;
  assets?: UpdateAsset[];
  error?: string;
}

export interface FleetState {
  hosts: HostRecord[];
  credentials: CredentialRecord[];
  profiles: SharingProfile[];
}

export interface HostDraft {
  id: string;
  name: string;
  address: string;
  username: string;
  port: number;
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

export interface CredentialDraft {
  name: string;
  kind: 'key' | 'password';
  secret: string;
  passphrase: string;
}

export interface ProfileDraft {
  id: string;
  name: string;
  gateway_id: string;
  client_ids: string[];
  port: number;
  endpoint: string;
  tunnel_cidr: string;
  preserve_routes: string;
  maintenance: boolean;
  proxy_mode: 'direct' | 'share';
  proxy_host: string;
  proxy_port: number;
}
