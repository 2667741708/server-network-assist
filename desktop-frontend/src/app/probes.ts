import type { HealthLevel, ProbeResult } from '../api/types';

export type ProbeRole = 'gateway' | 'client' | 'unknown';

export interface ProbeCapability {
  key: 'ssh' | 'dns' | 'internet' | 'helper' | 'role';
  label: string;
  detail: string;
  level: HealthLevel;
}

function capability(key: ProbeCapability['key'], value: boolean | null | undefined, label: string): ProbeCapability {
  if (value === true) return {key, label, detail: '正常', level: 'ok'};
  if (value === false) return {key, label, detail: 'Failed', level: 'danger'};
  return {key, label, detail: 'Unknown', level: 'info'};
}

export function probeCapabilities(probe: ProbeResult, role: ProbeRole = 'unknown', sharingState?: string): ProbeCapability[] {
  const clientBeforeEnable = role === 'client' && sharingState !== 'enabled';
  const internet = clientBeforeEnable
    ? {key: 'internet' as const, label: '公网', detail: '启用共享前不判定客户端公网', level: 'info' as const}
    : capability('internet', probe.internet, '公网');
  const roleSupported = role === 'gateway' ? probe.gateway_supported : role === 'client' ? probe.client_supported : undefined;
  const roleLabel = role === 'gateway' ? '出口能力' : role === 'client' ? '客户端能力' : '节点角色';
  const roleDetail = role === 'unknown'
    ? '未指定角色'
    : roleSupported === true ? '角色能力可用' : roleSupported === false ? '角色能力不可用' : 'Unknown';
  return [
    capability('ssh', probe.ssh, 'SSH'),
    capability('dns', probe.dns, 'DNS'),
    internet,
    capability('helper', probe.helper, 'Helper'),
    {key: 'role', label: roleLabel, detail: roleDetail, level: roleSupported === true ? 'ok' : roleSupported === false ? 'warning' : 'info'},
  ];
}

export function probeSummary(probe: ProbeResult, role: ProbeRole = 'unknown', sharingState?: string): string {
  return probeCapabilities(probe, role, sharingState).map(item => `${item.label} ${item.detail}`).join(' · ');
}
