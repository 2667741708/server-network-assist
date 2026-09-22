import { requestJson } from './client';
import type {
  FleetActionResponse,
  FleetAuditResponse,
  FleetCredentialsResponse,
  FleetHostsResponse,
  FleetNetworkResponse,
  HostInspectResponse,
  HostRecord,
  ProbeResponse,
  SharingProfile,
} from './types';

export const getHosts = () => requestJson<FleetHostsResponse>('fleet/hosts');
export const getCredentials = () => requestJson<FleetCredentialsResponse>('fleet/credentials');
export const getNetwork = () => requestJson<FleetNetworkResponse>('fleet/network');
export const getAudit = () => requestJson<FleetAuditResponse>('fleet/audit');

export const saveHost = (host: HostRecord) => requestJson<FleetActionResponse>('fleet/host/save', host);
export const deleteHost = (id: string) => requestJson<FleetActionResponse>('fleet/host/delete', {id});
export const inspectHost = (id: string) => requestJson<HostInspectResponse>('fleet/host/inspect', {id});

export const saveCredential = (payload: {name: string; kind: string; secret: string; passphrase: string}) =>
  requestJson<FleetActionResponse>('fleet/credential/save', payload);
export const deleteCredential = (id: string) => requestJson<FleetActionResponse>('fleet/credential/delete', {id});

export const probeHosts = (ids: string[]) => requestJson<ProbeResponse>('fleet/network/probe', {ids});
export const installHelper = (ids: string[]) => requestJson<FleetActionResponse>('fleet/network/helper/install', {ids});
export const saveProfile = (profile: SharingProfile) => requestJson<FleetActionResponse>('fleet/network/profile/save', profile);
export const enableProfile = (id: string) => requestJson<FleetActionResponse>('fleet/network/profile/enable', {id});
export const disableProfile = (id: string) => requestJson<FleetActionResponse>('fleet/network/profile/disable', {id});
export const deleteProfile = (id: string) => requestJson<FleetActionResponse>('fleet/network/profile/delete', {id});
